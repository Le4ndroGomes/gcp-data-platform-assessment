"""Data lineage e dependências (item 11).

collect(): consulta a Data Lineage API (Dataplex) para as tabelas mais acessadas.
build_graph(): monta (offline) o grafo Sistemas de origem -> Ingestão -> Armazenamento
-> Transformação -> BigQuery -> Modelos semânticos -> Power BI, a partir de todas as
evidências coletadas. Cada aresta recebe status:
  confirmed : observada em execução (JOBS), configuração explícita (DTS, Datastream,
              Pub/Sub, tabela externa, Dataform, navegação M) ou Data Lineage API
  inferred  : derivada de heurística (SQL nativo parseado, correlação Power BI x jobs)
  unknown   : dependência declarada sem destino/origem resolvível
"""
from __future__ import annotations

from collections import Counter, defaultdict

from core.context import Context
from utils.http import ApiError
from utils.logging import get_logger

log = get_logger("lineage")
MODULE = "lineage"
LINEAGE = "https://datalineage.googleapis.com/v1"


# ------------------------------------------------------------------ collect
def collect(ctx: Context) -> dict:
    if not ctx.config["lineage"].get("use_lineage_api", True):
        ctx.coverage.entry(MODULE, "lineage_api").status = "Not Assessed"
        return {"links": 0}
    cap = int(ctx.config["lineage"].get("max_lineage_api_entities", 200))
    tables: Counter = Counter()
    for f in (ctx.raw_dir / "bigquery_workloads").glob("workloads__*.json") if (ctx.raw_dir / "bigquery_workloads").exists() else []:
        data = ctx.load_raw("bigquery_workloads", f.stem) or {}
        for r in data.get("tables_read") or []:
            tables[(r["table_ref"], data["location"].lower())] += r.get("jobs") or 0
    links = []
    targets = [t for t, _ in tables.most_common(cap)]

    def per(item):
        ref, loc = item
        project = ref.split(".")[0]
        for direction in ("target", "source"):
            body = {direction: {"fullyQualifiedName": f"bigquery:{ref}"}, "pageSize": 100}
            try:
                for ln in ctx.gcp.paginate(f"{LINEAGE}/projects/{project}/locations/{loc}:searchLinks", "links",
                                           method="POST", body=body, max_items=500):
                    links.append({"source": (ln.get("source") or {}).get("fullyQualifiedName"),
                                  "target": (ln.get("target") or {}).get("fullyQualifiedName"),
                                  "start": ln.get("startTime"), "end": ln.get("endTime")})
                ctx.coverage.ok(MODULE, "lineage_api", 1)
            except ApiError as exc:
                ctx.coverage.fail(MODULE, "lineage_api", exc, scope=f"{project}/{loc}")
                return

    ctx.run_parallel(per, targets, "lineage_api")
    uniq = {(l["source"], l["target"]): l for l in links if l["source"] and l["target"]}
    ctx.save_raw(MODULE, "lineage_api_links", list(uniq.values()))
    if len(tables) > cap:
        ctx.coverage.limitation(MODULE, "lineage_api", f"Data Lineage API consultada para {cap} de {len(tables)} tabelas (mais lidas)")
    ctx.coverage.finalize(MODULE, "lineage_api", source="Data Lineage API searchLinks")
    return {"links": len(uniq)}


# ------------------------------------------------------------------- graph
class Graph:
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple[str, str, str], dict] = {}

    def node(self, nid: str, kind: str, layer: str, **attrs) -> str:
        n = self.nodes.setdefault(nid, {"id": nid, "kind": kind, "layer": layer, "label": attrs.pop("label", nid)})
        for k, v in attrs.items():
            if v is not None:
                n[k] = v
        return nid

    def edge(self, src: str, dst: str, relation: str, status: str, evidence: str, **attrs) -> None:
        if not src or not dst or src == dst:
            return
        key = (src, dst, relation)
        rank = {"confirmed": 3, "inferred": 2, "unknown": 1}
        cur = self.edges.get(key)
        if cur:
            if rank[status] > rank[cur["status"]]:
                cur["status"] = status
            if evidence not in cur["evidence"]:
                cur["evidence"].append(evidence)
            for k, v in attrs.items():
                if isinstance(v, (int, float)) and isinstance(cur.get(k), (int, float)):
                    cur[k] += v
                elif v is not None:
                    cur.setdefault(k, v)
            return
        self.edges[key] = {"source": src, "target": dst, "relation": relation, "status": status, "evidence": [evidence], **attrs}

    # ---------------------------------------------------------- analytics
    def adjacency(self) -> tuple[dict, dict]:
        out, inc = defaultdict(set), defaultdict(set)
        for (s, t, _), _e in self.edges.items():
            out[s].add(t)
            inc[t].add(s)
        return out, inc

    def sccs(self) -> list[list[str]]:
        """Tarjan iterativo: componentes fortemente conexos com >1 nó = dependências circulares."""
        out, _ = self.adjacency()
        index, low, on, stack, res, counter = {}, {}, set(), [], [], [0]
        for root in list(self.nodes):
            if root in index:
                continue
            work = [(root, iter(out[root]))]
            index[root] = low[root] = counter[0]
            counter[0] += 1
            stack.append(root)
            on.add(root)
            while work:
                v, it = work[-1]
                nxt = next(it, None)
                if nxt is not None:
                    if nxt not in index:
                        index[nxt] = low[nxt] = counter[0]
                        counter[0] += 1
                        stack.append(nxt)
                        on.add(nxt)
                        work.append((nxt, iter(out[nxt])))
                    elif nxt in on:
                        low[v] = min(low[v], index[nxt])
                    continue
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[v])
                if low[v] == index[v]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on.discard(w)
                        comp.append(w)
                        if w == v:
                            break
                    if len(comp) > 1:
                        res.append(sorted(comp))
        return res

    def depths(self) -> dict[str, int]:
        """Profundidade (maior caminho a partir de uma raiz) ignorando arestas de ciclos."""
        out, inc = self.adjacency()
        cyc = {n for c in self.sccs() for n in c}
        indeg = {n: len([p for p in inc[n] if p not in cyc or n not in cyc]) for n in self.nodes}
        depth = {n: 0 for n in self.nodes}
        queue = [n for n, d in indeg.items() if d == 0]
        seen = 0
        while queue:
            n = queue.pop()
            seen += 1
            for m in out[n]:
                if n in cyc and m in cyc:
                    continue
                depth[m] = max(depth[m], depth[n] + 1)
                indeg[m] -= 1
                if indeg[m] == 0:
                    queue.append(m)
        return depth

    def reachable(self, start: str, direction: str = "down", max_depth: int = 50) -> dict[str, int]:
        out, inc = self.adjacency()
        nb = out if direction == "down" else inc
        dist, frontier = {start: 0}, [start]
        while frontier:
            nxt = []
            for n in frontier:
                for m in nb[n]:
                    if m not in dist and dist[n] < max_depth:
                        dist[m] = dist[n] + 1
                        nxt.append(m)
            frontier = nxt
        dist.pop(start, None)
        return dist

    def critical_path(self) -> list[str]:
        depth = self.depths()
        if not depth:
            return []
        _, inc = self.adjacency()
        end = max(depth, key=lambda n: depth[n])
        path = [end]
        while depth[path[-1]] > 0:
            prev = [p for p in inc[path[-1]] if depth.get(p, -1) == depth[path[-1]] - 1]
            if not prev:
                break
            path.append(sorted(prev)[0])
        return list(reversed(path))

    def to_dict(self) -> dict:
        return {"nodes": list(self.nodes.values()), "edges": list(self.edges.values())}

    @classmethod
    def from_dict(cls, d: dict) -> "Graph":
        g = cls()
        for n in d.get("nodes", []):
            g.nodes[n["id"]] = n
        for e in d.get("edges", []):
            g.edges[(e["source"], e["target"], e["relation"])] = e
        return g


def _bq(ref: str) -> str:
    ref = (ref or "").replace("bigquery:", "").replace(":", ".").strip("`")
    return f"bq:{ref}"


def build_graph(ctx: Context, inventory: dict) -> Graph:
    """inventory: saída de analyzers.inventory.build_inventory (tabelas, pipelines, pbi...)."""
    g = Graph()
    tables = inventory.get("tables_by_ref", {})
    for ref, t in tables.items():
        kind = {"VIEW": "view", "MATERIALIZED VIEW": "materialized_view", "EXTERNAL": "external_table",
                "SNAPSHOT": "snapshot", "CLONE": "clone"}.get(t.get("table_type"), "table")
        g.node(_bq(ref), kind, "bigquery", label=ref, logical_bytes=t.get("total_logical_bytes"),
               dataset=".".join(ref.split(".")[:2]))
        for uri in t.get("external_uris") or []:
            bucket = uri.replace("gs://", "").split("/")[0]
            g.node(f"gcs:{bucket}", "bucket", "storage", label=f"gs://{bucket}")
            g.edge(f"gcs:{bucket}", _bq(ref), "external_table", "confirmed", "TABLE_OPTIONS.uris")
        if t.get("table_type") in ("SNAPSHOT", "CLONE") and t.get("base_table"):
            g.edge(_bq(t["base_table"]), _bq(ref), "snapshot_of", "confirmed", "INFORMATION_SCHEMA.TABLES base_table")

    # 1. Execução observada (JOBS): referenced_tables -> destination_table
    for e in inventory.get("job_edges", []):
        s, t = _bq(e["source_ref"]), _bq(e["target_ref"])
        g.node(s, "table", "bigquery", label=e["source_ref"])
        g.node(t, "table", "bigquery", label=e["target_ref"])
        g.edge(s, t, "transforms_to", "confirmed", "INFORMATION_SCHEMA.JOBS", jobs=e.get("jobs", 0),
               statement_type=e.get("statement_type"), last_seen=e.get("last_seen"))
    # 2. Views (modo full: definição SQL parseada)
    for v in inventory.get("views", []):
        for ref in v.get("referenced_tables") or []:
            full = ref if ref.count(".") == 2 else f"{v['project_id']}.{ref}" if ref.count(".") == 1 else None
            if full:
                g.edge(_bq(full), _bq(v["ref"]), "view_definition", "confirmed", "VIEWS.view_definition (SQLGlot)")
    # 3. Dataform
    for e in inventory.get("dataform_edges", []):
        g.edge(_bq(e["source"]), _bq(e["target"]), "transforms_to", "confirmed", "Dataform compilation graph")
    # 4. Pipelines (configuração)
    for p in inventory.get("pipelines", []):
        pid = f"pipeline:{p['technology']}:{p['name']}"
        g.node(pid, "pipeline", "ingestion" if p.get("load_strategy") in ("CDC", "streaming") or "DTS" in p["technology"]
               or "Datastream" in p["technology"] or "Pub/Sub" in p["technology"] else "transformation",
               label=f"{p['technology']}: {p['name']}", technology=p["technology"], frequency=p.get("frequency"),
               avg_duration_s=p.get("avg_duration_s"), failure_rate=p.get("failure_rate"))
        for s in p.get("sources") or []:
            sid = _source_node(g, s)
            if sid:
                g.edge(sid, pid, "feeds", "confirmed" if p.get("confidence") == "high" else "inferred",
                       f"config {p['technology']}")
        for t in p.get("targets") or []:
            tid = _source_node(g, t)
            if tid:
                g.edge(pid, tid, "writes", "confirmed" if p.get("confidence") == "high" else "inferred",
                       f"config {p['technology']}")
        if not p.get("sources") and not p.get("targets") and p["technology"] not in ("Cloud Scheduler",):
            g.edge(f"unknown:{p['technology']}", pid, "feeds", "unknown", "origem não resolvida")
            g.node(f"unknown:{p['technology']}", "unknown", "source", label="origem não identificada")
    # 5. Composer/Airflow e outros via labels de jobs (airflow-dag, dataform, data_source_id)
    for w in inventory.get("labeled_writes", []):
        pid = f"pipeline:{w['technology']}:{w['name']}"
        g.node(pid, "pipeline", "transformation", label=f"{w['technology']}: {w['name']}", technology=w["technology"])
        g.edge(pid, _bq(w["table_ref"]), "writes", "confirmed", f"JOBS label {w['label']}", jobs=w.get("jobs", 0))
    # 6. Data Lineage API
    for ln in inventory.get("lineage_api_links", []):
        s, t = ln["source"], ln["target"]
        sid = _bq(s) if s.startswith("bigquery:") else _source_node(g, s)
        tid = _bq(t) if t.startswith("bigquery:") else _source_node(g, t)
        if sid and tid:
            g.node(sid, "table", "bigquery") if sid.startswith("bq:") else None
            g.node(tid, "table", "bigquery") if tid.startswith("bq:") else None
            g.edge(sid, tid, "lineage", "confirmed", "Data Lineage API")
    # 7. Power BI
    for m in inventory.get("pbi_models", []):
        mid = f"pbi_model:{m['id']}"
        g.node(mid, "semantic_model", "semantic", label=f"Modelo: {m['name']}", storage_mode=m.get("storage_mode"))
        for ref in m.get("bigquery_refs_explicit") or []:
            g.node(_bq(ref), "table", "bigquery", label=ref)
            g.edge(_bq(ref), mid, "consumed_by", "confirmed", "Power Query (navegação explícita)")
        for ref in m.get("bigquery_refs_native") or []:
            g.node(_bq(ref), "table", "bigquery", label=ref)
            g.edge(_bq(ref), mid, "consumed_by", "inferred", "SQL nativo parseado")
        for c in m.get("correlated_tables") or []:
            g.node(_bq(c["table_ref"]), "table", "bigquery", label=c["table_ref"])
            g.edge(_bq(c["table_ref"]), mid, "consumed_by", "inferred", f"correlação jobs ({c['confidence']})")
        for st in m.get("other_sources") or []:
            sid = g.node(f"external:{st}", "external_source", "source", label=f"Fonte externa: {st}")
            g.edge(sid, mid, "consumed_by", "confirmed", "Power Query (conector)")
        for up in m.get("upstream_datasets") or []:
            g.edge(f"pbi_model:{up}", mid, "composite_of", "confirmed", "Scanner upstreamDatasets")
    for r in inventory.get("pbi_reports", []):
        rid = f"pbi_report:{r['id']}"
        g.node(rid, "report", "consumption", label=f"Relatório: {r['name']}", views=r.get("views"))
        if r.get("dataset_id"):
            g.edge(f"pbi_model:{r['dataset_id']}", rid, "serves", "confirmed", "Scanner report.datasetId")
    for d in inventory.get("pbi_dashboards", []):
        did = f"pbi_dashboard:{d['id']}"
        g.node(did, "dashboard", "consumption", label=f"Dashboard: {d['name']}")
        for ds in d.get("tile_datasets") or []:
            g.edge(f"pbi_model:{ds}", did, "serves", "confirmed", "Scanner dashboard tiles")
    # Remove nós referenciados apenas por aresta sem cadastro
    for (s, t, _r) in list(g.edges):
        for n in (s, t):
            if n not in g.nodes:
                kind = n.split(":", 1)[0]
                g.node(n, kind, {"bq": "bigquery", "pbi_model": "semantic", "gcs": "storage"}.get(kind, "source"),
                       label=n.split(":", 1)[-1])
    return g


def _source_node(g: Graph, ref: str) -> str | None:
    if not ref:
        return None
    if ref.startswith("bigquery:") or ref.count(".") == 2 and ":" not in ref:
        r = ref.replace("bigquery:", "")
        if "(" in r:
            return None
        if r.count(".") == 1:  # dataset
            nid = f"bqds:{r}"
            g.node(nid, "dataset", "bigquery", label=f"dataset {r}")
            return nid
        nid = _bq(r)
        g.node(nid, "table", "bigquery", label=r)
        return nid
    if ref.startswith("gcs:"):
        b = ref[4:].replace("gs://", "").split("/")[0]
        return g.node(f"gcs:{b}", "bucket", "storage", label=f"gs://{b}")
    if ref.startswith("pubsub:"):
        return g.node(ref, "topic", "ingestion", label=ref)
    if ref.startswith(("external:", "evento:")):
        return g.node(ref, "external_source", "source", label=ref.replace("external:", "Fonte externa: "))
    if ref.startswith(("spanner:", "bigtable:")):
        return g.node(ref, "database", "source", label=ref)
    if ref.count(".") == 1:
        nid = f"bqds:{ref}"
        return g.node(nid, "dataset", "bigquery", label=f"dataset {ref}")
    return g.node(f"service:{ref}", "service", "orchestration", label=ref)


def graph_metrics(g: Graph, inventory: dict) -> dict:
    out, inc = g.adjacency()
    depth = g.depths()
    cycles = g.sccs()
    consumption = {n for n, v in g.nodes.items() if v["kind"] in ("report", "dashboard", "semantic_model")}
    fan = [{"node": n, "label": g.nodes[n].get("label"), "kind": g.nodes[n]["kind"], "fan_in": len(inc[n]),
            "fan_out": len(out[n])} for n in g.nodes]
    spof = []
    for n, v in g.nodes.items():
        if v["kind"] in ("table", "view", "materialized_view", "pipeline", "bucket", "external_source", "dataset"):
            down = g.reachable(n, "down")
            hit = [d for d in down if d in consumption]
            if len(hit) >= 2:
                spof.append({"node": n, "label": v.get("label"), "kind": v["kind"], "downstream_bi_assets": len(hit),
                             "downstream_total": len(down)})
    read_refs = inventory.get("tables_read_refs", set())
    orphans = [n for n, v in g.nodes.items() if v["kind"] in ("table", "view", "materialized_view") and not out[n]
               and v.get("label") not in read_refs]
    status = Counter(e["status"] for e in g.edges.values())
    return {
        "nodes": len(g.nodes), "edges": len(g.edges), "edge_status": dict(status),
        "node_kinds": dict(Counter(v["kind"] for v in g.nodes.values())),
        "max_depth": max(depth.values()) if depth else 0,
        "depth_distribution": dict(Counter(depth.values())),
        "top_fan_in": sorted(fan, key=lambda x: -x["fan_in"])[:20],
        "top_fan_out": sorted(fan, key=lambda x: -x["fan_out"])[:20],
        "cycles": cycles[:50], "cycle_count": len(cycles),
        "single_points_of_failure": sorted(spof, key=lambda x: -x["downstream_bi_assets"])[:30],
        "objects_without_consumption_evidence": len(orphans),
        "orphan_sample": [g.nodes[n].get("label") for n in orphans[:50]],
        "external_dependencies": sorted({v.get("label") for v in g.nodes.values() if v["kind"] in ("external_source", "unknown")}),
        "critical_path": [g.nodes[n].get("label") for n in g.critical_path()],
    }


# ------------------------------------------------------------------ export
def to_graphml(g: Graph) -> str:
    from xml.sax.saxutils import escape

    keys = ["kind", "layer", "label"]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">']
    for k in keys:
        lines.append(f'  <key id="{k}" for="node" attr.name="{k}" attr.type="string"/>')
    for k in ("relation", "status", "evidence"):
        lines.append(f'  <key id="e_{k}" for="edge" attr.name="{k}" attr.type="string"/>')
    lines.append('  <graph id="lineage" edgedefault="directed">')
    for n in g.nodes.values():
        lines.append(f'    <node id="{escape(n["id"], {chr(34): "&quot;"})}">' +
                     "".join(f'<data key="{k}">{escape(str(n.get(k, "")))}</data>' for k in keys) + "</node>")
    for i, e in enumerate(g.edges.values()):
        lines.append(f'    <edge id="e{i}" source="{escape(e["source"], {chr(34): "&quot;"})}" target="{escape(e["target"], {chr(34): "&quot;"})}">'
                     f'<data key="e_relation">{escape(e["relation"])}</data><data key="e_status">{e["status"]}</data>'
                     f'<data key="e_evidence">{escape("; ".join(e["evidence"]))}</data></edge>')
    lines += ["  </graph>", "</graphml>"]
    return "\n".join(lines)


def to_mermaid(g: Graph, max_nodes: int = 80) -> str:
    """Mermaid com os nós mais conectados (grafos grandes ficam ilegíveis)."""
    out, inc = g.adjacency()
    ranked = sorted(g.nodes, key=lambda n: -(len(out[n]) + len(inc[n])))[:max_nodes]
    keep = set(ranked)
    ids = {n: f"n{i}" for i, n in enumerate(ranked)}
    shapes = {"report": ("[/", "/]"), "dashboard": ("[/", "/]"), "semantic_model": ("[[", "]]"), "pipeline": ("{{", "}}"),
              "external_source": ("[(", ")]"), "bucket": ("[(", ")]"), "topic": (">", "]")}
    lines = ["flowchart LR"]
    for n in ranked:
        a, b = shapes.get(g.nodes[n]["kind"], ("[", "]"))
        label = str(g.nodes[n].get("label", n)).replace('"', "'")[:60]
        lines.append(f'  {ids[n]}{a}"{label}"{b}')
    for e in g.edges.values():
        if e["source"] in keep and e["target"] in keep:
            arrow = "-->" if e["status"] == "confirmed" else "-.->"
            lines.append(f"  {ids[e['source']]} {arrow} {ids[e['target']]}")
    if len(g.nodes) > max_nodes:
        lines.append(f"  %% {len(g.nodes) - max_nodes} nós omitidos (ver data_lineage.json / .graphml)")
    return "\n".join(lines)
