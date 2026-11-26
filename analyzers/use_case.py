"""Assessment por caso de uso e ranking de candidatos a POC (item 20).

A partir de sementes (relatório/modelo Power BI, dataset ou tabelas BigQuery),
percorre o grafo de lineage (upstream e/ou downstream) e calcula métricas do
subgrafo. Dependências compartilhadas são contadas uma única vez (conjuntos).
Cada percentual informa explicitamente o denominador utilizado.
"""
from __future__ import annotations

from collectors.lineage import Graph

GiB = 1024 ** 3


def _seed_nodes(g: Graph, scope: dict, inv: dict) -> tuple[list[str], list[str]]:
    seeds, missing = [], []
    s = scope.get("seeds") or {}
    by_label = {}
    for nid, n in g.nodes.items():
        by_label.setdefault(str(n.get("label", "")).lower(), []).append(nid)
    for name in s.get("powerbi_reports") or []:
        hits = [f"pbi_report:{r['id']}" for r in inv.get("pbi_reports") or [] if name in (r.get("id"), r.get("name"))]
        (seeds.extend(hits) if hits else missing.append(f"relatório {name}"))
    for name in s.get("powerbi_datasets") or s.get("powerbi_models") or []:
        hits = [f"pbi_model:{m['id']}" for m in inv.get("pbi_models") or [] if name in (m.get("id"), m.get("name"))]
        (seeds.extend(hits) if hits else missing.append(f"modelo {name}"))
    for ds in s.get("bigquery_datasets") or []:
        hits = [nid for nid in g.nodes if nid.startswith(f"bq:{ds}.")]
        (seeds.extend(hits) if hits else missing.append(f"dataset {ds}"))
    for t in s.get("bigquery_tables") or []:
        nid = f"bq:{t}"
        (seeds.append(nid) if nid in g.nodes else missing.append(f"tabela {t}"))
    return sorted(set(seeds)), missing


def evaluate(g: Graph, inv: dict, scope: dict, platform: dict) -> dict:
    seeds, missing = _seed_nodes(g, scope, inv)
    direction = (scope.get("direction") or "both").lower()
    depth = int(scope.get("max_depth") or 25)
    nodes = set(seeds)
    for s in seeds:
        if direction in ("both", "upstream"):
            nodes |= set(g.reachable(s, "up", depth))
        if direction in ("both", "downstream"):
            nodes |= set(g.reachable(s, "down", depth))
    return _metrics(g, inv, nodes, platform, scope.get("name") or "caso de uso", seeds, missing, direction)


def _metrics(g: Graph, inv: dict, nodes: set[str], platform: dict, name: str, seeds: list, missing: list, direction: str) -> dict:
    tables = inv.get("tables_by_ref", {})
    tnodes = [n for n in nodes if n.startswith("bq:")]
    trefs = {n[3:] for n in tnodes}
    tdetail = [tables[r] for r in trefs if r in tables]
    physical = [t for t in tdetail if t.get("table_type") in ("BASE TABLE", "MATERIALIZED VIEW")]
    views = [t for t in tdetail if t.get("table_type") == "VIEW"]
    pnodes = [n for n in nodes if n.startswith("pipeline:")]
    pipes = [g.nodes[n] for n in pnodes]
    routines = [r for r in inv.get("routines") or [] if any(r["ref"].startswith(".".join(t.split(".")[:2]) + ".") for t in trefs)]
    vol = sum(t.get("total_logical_bytes") or 0 for t in physical)
    rpd = sum(p.get("runs_per_day") or 0 for p in [x for x in (inv.get("pipelines") or [])
                                                   if f"pipeline:{x['technology']}:{x['name']}" in nodes])
    chain_s = sum(p.get("avg_duration_s") or 0 for p in pipes)
    sub = Graph()
    sub.nodes = {n: g.nodes[n] for n in nodes}
    sub.edges = {k: e for k, e in g.edges.items() if e["source"] in nodes and e["target"] in nodes}
    crit = [g.nodes[n].get("label") for n in sub.critical_path()]
    slot_uc = bytes_uc = 0
    slot_all = bytes_all = 0
    for w in inv.get("workloads") or []:
        for f in w.get("fingerprints") or []:
            slot_all += f.get("slot_ms") or 0
            bytes_all += f.get("bytes_billed") or 0
            if trefs & set(f.get("refs") or []):
                slot_uc += f.get("slot_ms") or 0
                bytes_uc += f.get("bytes_billed") or 0
    total_vol = (platform.get("volume") or {}).get("logical", {}).get("bytes") or 0
    total_tables = (platform.get("volume") or {}).get("tables_counted") or 0
    total_pipes = (platform.get("pipelines") or {}).get("total") or 0
    complexities = [o["complexity"] for o in (platform.get("sql") or {}).get("objects") or []
                    if o.get("complexity") and (o.get("ref") in trefs or trefs & set(o.get("refs") or []))]
    order = ["Baixa", "Média", "Alta", "Muito alta"]
    worst = max(complexities, key=order.index) if complexities else None
    models = [g.nodes[n] for n in nodes if n.startswith("pbi_model:")]
    reports = [n for n in nodes if n.startswith("pbi_report:")]
    est_cost = None
    pe = platform.get("pricing", {})
    if bytes_uc:
        est_cost = round(bytes_uc / 1024 ** 4 * pe.get("bq_on_demand_per_tib", 6.25) * 30 / max(platform.get("window_days") or 30, 1), 2)

    def pct(a, b):
        return round(100 * a / b, 2) if b else None

    return {
        "name": name, "seeds": seeds, "seeds_not_found": missing, "direction": direction,
        "tables": len(physical), "views": len(views), "routines_in_same_datasets": len(routines), "pipelines": len(pipes),
        "semantic_models": len(models), "reports": len(reports), "nodes": len(nodes), "edges": len(sub.edges),
        "logical_bytes": vol, "logical_GiB": round(vol / GiB, 2), "pipeline_runs_per_day": round(rpd, 2),
        "chain_duration_s_sum": round(chain_s, 1), "critical_path": crit, "worst_sql_complexity": worst,
        "attributable": {"slot_hours_window": round(slot_uc / 3.6e6, 2), "bytes_billed_window": bytes_uc,
                         "estimated_on_demand_month": est_cost,
                         "note": "Atribuição por fingerprints que referenciam ao menos uma tabela do caso de uso (consultas que também tocam outras tabelas contam integralmente)."},
        "share_of_platform": {
            "storage_pct": pct(vol, total_vol), "storage_denominator": "volume lógico total BASE TABLE + MATERIALIZED VIEW",
            "processing_pct": pct(slot_uc, slot_all), "processing_denominator": "slot-ms das top fingerprints coletadas (não do total da plataforma)",
            "objects_pct": pct(len(physical), total_tables), "objects_denominator": "tabelas físicas inventariadas",
            "pipelines_pct": pct(len(pipes), total_pipes), "pipelines_denominator": "pipelines inventariados"},
        "migration_complexity": worst or ("Média" if pipes else "Baixa"),
    }


def poc_candidates(g: Graph, inv: dict, platform: dict, top: int = 5) -> list[dict]:
    """Ranqueia modelos Power BI dependentes de BigQuery como candidatos a POC."""
    out = []
    for m in inv.get("pbi_models") or []:
        nid = f"pbi_model:{m['id']}"
        if nid not in g.nodes or not m.get("uses_bigquery"):
            continue
        nodes = {nid} | set(g.reachable(nid, "up", 25)) | set(g.reachable(nid, "down", 3))
        r = _metrics(g, inv, nodes, platform, m["name"], [nid], [], "both")
        views = sum((rep.get("views") or 0) for rep in inv.get("pbi_reports") or [] if rep.get("dataset_id") == m["id"])
        order = {"Baixa": 0, "Média": 1, "Alta": 2, "Muito alta": 3, None: 1}
        size_pen = 0 if r["logical_GiB"] < 500 else 1 if r["logical_GiB"] < 5000 else 2
        score = 10 - 2 * order.get(r["worst_sql_complexity"], 1) - size_pen - min(3, r["pipelines"] / 5) + min(3, views / 500) \
            + (1 if m.get("storage_mode", "").startswith("Import") else 0)
        out.append({**r, "storage_mode": m.get("storage_mode"), "report_views": views, "poc_fit_score": round(score, 2),
                    "rationale": "Menor complexidade SQL, volume moderado, poucos pipelines e uso relevante pontuam melhor."})
    return sorted(out, key=lambda x: -x["poc_fit_score"])[:top]
