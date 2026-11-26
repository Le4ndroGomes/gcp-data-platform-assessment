"""Platform footprint: habilitado x provisionado x uso efetivo (item 3) e arquitetura AS-IS."""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from collectors.discovery import summarize_assets
from collectors.service_catalog import SERVICE_CATALOG
from utils.fmt import n

BILLING_NAMES = {
    "bigquery": r"^BigQuery", "biglake": r"BigLake", "gcs": r"^Cloud Storage$", "composer": r"Composer",
    "dataflow": r"^Dataflow", "dataproc": r"Dataproc", "dataform": r"Dataform", "datafusion": r"Data Fusion",
    "pubsub": r"Pub/Sub", "datastream": r"Datastream", "cloudsql": r"^Cloud SQL", "alloydb": r"AlloyDB",
    "spanner": r"Spanner", "functions": r"Cloud Functions|Cloud Run functions", "cloudrun": r"^Cloud Run$",
    "compute": r"^Compute Engine", "gke": r"Kubernetes Engine", "vertex": r"Vertex AI|AI Platform|Notebooks",
    "dataplex": r"Dataplex|Data Catalog", "scheduler": r"Cloud Scheduler", "workflows": r"^Workflows", "looker": r"Looker",
}


def _collector_counts(inv: dict) -> dict[str, dict]:
    """Recursos e atividade observados pelos collectors específicos."""
    c: dict[str, dict] = defaultdict(lambda: {"resources": 0, "activity": 0, "activity_label": "", "active_state": 0})
    pipes = inv.get("pipelines") or []
    tech_map = {"Cloud Composer": "composer", "Dataflow": "dataflow", "Dataproc": "dataproc", "Dataform": "dataform",
                "Cloud Data Fusion": "datafusion", "Pub/Sub": "pubsub", "Datastream": "datastream",
                "Cloud Scheduler": "scheduler", "Workflows": "workflows", "Cloud Functions": "functions", "Cloud Run job": "cloudrun"}
    for p in pipes:
        key = next((v for k, v in tech_map.items() if p["technology"].startswith(k)), None)
        if key:
            c[key]["resources"] += 1
            c[key]["activity"] += p.get("runs_observed") or 0
            c[key]["activity_label"] = "execuções observadas"
            if not p.get("runs_observed") and str(p.get("state") or "").upper() in ("ENABLED", "ACTIVE", "RUNNING", "JOB_STATE_RUNNING"):
                c[key]["active_state"] += 1
    tables = [t for t in (inv.get("tables_by_ref") or {}).values() if not t.get("deleted")]
    c["bigquery"]["resources"] = len(tables)
    c["bigquery"]["activity"] = sum(sum(h.get("jobs") or 0 for h in w.get("hourly") or []) for w in inv.get("workloads") or [])
    c["bigquery"]["activity_label"] = "jobs na janela"
    c["biglake"]["resources"] = sum(1 for t in tables if t.get("table_type") == "EXTERNAL")
    c["gcs"]["resources"] = len(inv.get("buckets") or [])
    for pid, s in (inv.get("services") or {}).items():
        for key, items in (("cloudsql", s.get("cloudsql")), ("alloydb", s.get("alloydb")), ("spanner", s.get("spanner")),
                           ("compute", s.get("compute")), ("gke", s.get("gke")), ("looker", s.get("looker"))):
            c[key]["resources"] += len(items or [])
            c[key]["activity"] += sum(1 for i in items or [] if (i.get("state") or i.get("status")) in ("RUNNABLE", "READY", "RUNNING", "ACTIVE"))
            c[key]["activity_label"] = "instâncias em execução"
        v = s.get("vertex_ai") or {}
        c["vertex"]["resources"] += len(v.get("endpoints") or []) + (v.get("models") or 0) + len(v.get("workbench") or [])
        c["vertex"]["activity"] += v.get("pipeline_jobs") or 0
        c["vertex"]["activity_label"] = "pipeline jobs"
        d = s.get("dataplex") or {}
        c["dataplex"]["resources"] += len(d.get("lakes") or []) + len(d.get("data_scans") or [])
    for pid, fr in (inv.get("functions_run") or {}).items():
        c["functions"]["resources"] += len(fr.get("functions") or [])
        c["cloudrun"]["resources"] += len(fr.get("run_services") or []) + len(fr.get("run_jobs") or [])
    return c


def analyze(inv: dict, cost: dict) -> dict:
    disc = inv.get("discovery") or {}
    enabled = disc.get("services_enabled") or {}
    assets = summarize_assets(disc.get("assets") or {})
    api_usage = defaultdict(int)
    for pid, u in (disc.get("api_usage") or {}).items():
        for api, cnt in (u.get("requests_by_api") or {}).items():
            api_usage[api] += cnt
    billed = {s["service"]: s["net_cost"] for s in ((cost.get("billed") or {}).get("by_service_last_month") or [])}
    counts = _collector_counts(inv)
    discovery_ran = bool(enabled) or bool(disc.get("assets"))
    rows = []
    for svc in SERVICE_CATALOG:
        k = svc["key"]
        en_projects = sorted(p for p, apis in enabled.items() if any(a in apis for a in svc["apis"]))
        prov_assets = (assets.get(k) or {}).get("count", 0)
        prov = max(prov_assets, counts[k]["resources"])
        reqs = sum(api_usage.get(a, 0) for a in svc["apis"])
        cost_v = sum(v for name, v in billed.items() if re.search(BILLING_NAMES.get(k, "^$"), name))
        evidence = []
        if counts[k]["activity"]:
            evidence.append(f"{n(counts[k]['activity'])} {counts[k]['activity_label']}")
        if counts[k].get("active_state"):
            evidence.append(f"{n(counts[k]['active_state'])} recursos ativos (estado)")
        if reqs:
            evidence.append(f"{n(reqs)} chamadas de API")
        if cost_v > 0:
            evidence.append(f"custo faturado último mês {n(cost_v, 2)}")
        if evidence:
            status = "Em uso (evidência)"
        elif prov:
            status = "Provisionado sem evidência de uso"
        elif en_projects:
            status = "Habilitado sem recursos encontrados"
        elif discovery_ran:
            status = "Não habilitado"
        else:
            status = "Sem visibilidade"
        rows.append({"key": k, "service": svc["name"], "enabled_projects": len(en_projects), "provisioned": prov,
                     "usage_evidence": "; ".join(evidence) or "—", "status": status,
                     "locations": sorted((assets.get(k) or {}).get("locations", {}).keys())[:8],
                     "databricks_target": svc["target"], "evidence_source": svc["evidence"]})
    other_apis = Counter()
    known = {a for s in SERVICE_CATALOG for a in s["apis"]}
    for apis in enabled.values():
        for a in apis:
            if a not in known:
                other_apis[a] += 1
    return {"services": rows, "other_enabled_apis": dict(other_apis.most_common(40)),
            "projects": len(inv.get("projects") or []),
            "projects_selected": sum(1 for p in inv.get("projects") or [] if p.get("selected")),
            "environments": dict(Counter(p.get("environment") for p in inv.get("projects") or [] if p.get("selected"))),
            "locations": dict(Counter(loc for p in inv.get("projects") or [] for loc in p.get("bigquery_locations") or [])),
            "organizations": len(disc.get("organizations") or []), "folders": len(disc.get("folders") or []),
            "billing_accounts": len(disc.get("billing_accounts") or []),
            "service_accounts": sum(len(v or []) for v in (disc.get("service_accounts") or {}).values()),
            "network": _network_summary(disc.get("networks") or {})}


def _network_summary(nets: dict) -> dict:
    out = Counter()
    for pid, net in nets.items():
        out["vpcs"] += len(net.get("networks") or [])
        out["subnets"] += len(net.get("subnetworks") or [])
        out["subnets_private_google_access"] += sum(1 for s in net.get("subnetworks") or [] if s.get("private_google_access"))
        out["vpn_tunnels"] += len(net.get("vpn_tunnels") or [])
        out["interconnect_attachments"] += len(net.get("interconnect_attachments") or [])
        out["cloud_nat"] += net.get("cloud_nat_count") or 0
        out["shared_vpc_hosts"] += int(bool(net.get("shared_vpc_host")))
        out["shared_vpc_service_projects"] += int(bool(net.get("shared_vpc_host_project")))
        out["private_services_access"] += int(bool(net.get("private_services_access")))
        out["peerings"] += sum(len(v.get("peerings") or []) for v in net.get("networks") or [])
    return dict(out)


TECH_KEY = {"Cloud Composer": "composer", "Dataflow": "dataflow", "Dataproc": "dataproc", "Dataform": "dataform",
            "Cloud Data Fusion": "datafusion", "Pub/Sub": "pubsub", "Datastream": "datastream", "Cloud Scheduler": "scheduler",
            "Workflows": "workflows", "Cloud Functions": "functions", "Cloud Run": "cloudrun", "BigQuery DTS": "bigquery"}


def architecture_mermaid(footprint: dict, inv: dict, pbi: dict, pipelines: dict) -> str:
    """Diagrama AS-IS por camadas. Origens externas ligam-se à tecnologia que as consome (evidência de configuração)."""
    active = {r["key"]: r for r in footprint["services"] if r["status"] in ("Em uso (evidência)", "Provisionado sem evidência de uso")}

    def lab(key, extra=""):
        r = active[key]
        flag = "" if r["status"].startswith("Em uso") else " (sem evidência de uso)"
        return f'{r["service"]}{flag}{extra}'.replace('"', "'")

    ext_to: dict[str, set] = defaultdict(set)
    for p in inv.get("pipelines") or []:
        key = next((v for k, v in TECH_KEY.items() if p["technology"].startswith(k)), None)
        for src in p.get("sources") or []:
            if src.startswith("external:"):
                ext_to[src.split(":")[1]].add(key if key in active else "bigquery")
    for m in pbi.get("semantic_models") or []:
        for st in set(m.get("source_types") or []) - {"bigquery"}:
            ext_to[st].add("powerbi")
    layers = [("L1", "Ingestão", ["datastream", "pubsub", "datafusion", "functions", "cloudrun"]),
              ("L2", "Armazenamento", ["gcs", "biglake", "bigquery"]),
              ("L3", "Transformação e orquestração", ["dataform", "composer", "dataflow", "dataproc", "scheduler", "workflows"]),
              ("L4", "Governança", ["dataplex"]), ("L5", "Consumo", ["looker", "vertex"])]
    lines = ["flowchart LR"]
    dbs = [k for k in ("cloudsql", "alloydb", "spanner") if k in active]
    ext_ids = {e: f"ext{i}" for i, e in enumerate(sorted(ext_to)[:10])}
    lines.append('  subgraph L0["Origens"]')
    for e, eid in ext_ids.items():
        lines.append(f'    {eid}[("{e}")]')
    for k in dbs:
        lines.append(f'    {k}[("{lab(k)}")]')
    if not ext_ids and not dbs:
        lines.append('    src0[("Origens não identificadas — investigar")]')
    lines.append("  end")
    has_pbi = bool(pbi.get("semantic_models") or pbi.get("reports"))
    for lid, name, keys in layers:
        present = [k for k in keys if k in active]
        if lid == "L5" and has_pbi:
            present.append("powerbi")
        if not present:
            continue
        lines.append(f'  subgraph {lid}["{name}"]')
        for k in present:
            if k == "powerbi":
                lines.append(f'    powerbi[/"Power BI — {len(pbi.get("semantic_models") or [])} modelos, {len(pbi.get("reports") or [])} relatórios"/]')
            else:
                extra = f" — {len(inv.get('tables_by_ref') or {})} objetos" if k == "bigquery" else ""
                lines.append(f'    {k}["{lab(k, extra)}"]')
        lines.append("  end")
    if "bigquery" in active:
        for k in ("datastream", "pubsub", "datafusion", "functions", "dataflow", "dataproc", "dataform", "composer"):
            if k in active:
                lines.append(f"  {k} --> bigquery")
        if "gcs" in active:
            lines.append("  gcs --> bigquery")
        if has_pbi:
            lines.append("  bigquery --> powerbi")
        for k in ("looker", "vertex"):
            if k in active:
                lines.append(f"  bigquery --> {k}")
    if "pubsub" in active and "dataflow" in active:
        lines.append("  pubsub --> dataflow")
    for k in dbs:
        lines.append(f"  {k} -.->|fonte operacional| bigquery")
    for e, eid in ext_ids.items():
        for tgt in sorted(t for t in ext_to[e] if t and (t in active or t == "powerbi")):
            lines.append(f"  {eid} -.-> {tgt}")
    if "scheduler" in active:
        for t in sorted({p["targets"][0] for p in inv.get("pipelines") or [] if p["technology"] == "Cloud Scheduler" and p.get("targets")}):
            if t in active and t != "scheduler":
                lines.append(f"  scheduler -.-> {t}")
    return "\n".join(lines)


def powerbi_summary(inv: dict) -> dict:
    pbi = inv.get("powerbi") or {}
    models = inv.get("pbi_models") or []
    reports = inv.get("pbi_reports") or []
    refresh = pbi.get("refresh") or {}
    hist = refresh.get("refresh_history") or []
    fails = [h for h in hist if (h.get("status") or "").lower() == "failed"]
    durs = sorted(h["duration_s"] for h in hist if h.get("duration_s"))
    by_model_refresh = defaultdict(list)
    for h in hist:
        if h.get("duration_s"):
            by_model_refresh[h["dataset_id"]].append(h["duration_s"])
    for r in refresh.get("refreshables") or []:
        if r.get("avg_duration_s"):
            by_model_refresh[r["dataset_id"]].append(r["avg_duration_s"])
    bottlenecks = []
    for m in models:
        d = by_model_refresh.get(m["id"])
        if d and max(d) > 1800:
            bottlenecks.append({"model": m["name"], "max_refresh_s": round(max(d)), "storage_mode": m["storage_mode"]})
    simplify, push_down = [], []
    for m in models:
        reasons = []
        if m.get("calculated_columns", 0) >= 20:
            reasons.append(f"{m['calculated_columns']} colunas calculadas")
        if m.get("bidirectional_relationships", 0) >= 3:
            reasons.append(f"{m['bidirectional_relationships']} relacionamentos bidirecionais")
        if (m.get("dax_complexity") or {}).get("Muito alta", 0) + (m.get("dax_complexity") or {}).get("Alta", 0) >= 10:
            reasons.append("muitas medidas DAX complexas")
        if m.get("table_count", 0) >= 40:
            reasons.append(f"{m['table_count']} tabelas")
        if reasons:
            simplify.append({"model": m["name"], "reasons": reasons})
        heavy = sum((m.get("heavy_m_steps") or {}).values())
        if heavy >= 5 or m.get("calculated_tables") or m.get("native_query_count"):
            push_down.append({"model": m["name"], "heavy_m_steps": heavy, "calculated_tables": m.get("calculated_tables"),
                              "native_queries": m.get("native_query_count"),
                              "suggestion": "Mover joins/agrupamentos/colunas calculadas para tabelas gold no Databricks (SQL/pipelines)"})
    # relatórios potencialmente redundantes: mesmo modelo + nomes semelhantes, ou modelos com mesmas tabelas BQ
    sig = defaultdict(list)
    for m in models:
        if m.get("bigquery_refs_explicit"):
            sig[tuple(sorted(m["bigquery_refs_explicit"]))].append(m["name"])
    redundant_models = [v for v in sig.values() if len(v) > 1]
    name_groups = defaultdict(list)
    for r in reports:
        base = re.sub(r"(\s*[-_(]?\s*(v\d+|copy|c[oó]pia|old|teste|test|backup|bkp)\)?\s*)+$", "", (r.get("name") or "").lower()).strip()
        name_groups[base].append(r.get("name"))
    redundant_reports = [v for v in name_groups.values() if len(v) > 1]
    unused = [r["name"] for r in reports if r.get("views") in (None, 0)] if (pbi.get("activity") or {}).get("window_days") else []
    modes = Counter(m.get("storage_mode") for m in models)
    bq_models = [m for m in models if m.get("uses_bigquery")]
    projects_used = Counter(ref.split(".")[0] for m in bq_models for ref in m.get("bigquery_refs_explicit", []) + m.get("bigquery_refs_native", []) if ref.count(".") == 2)
    datasets_used = Counter(".".join(ref.split(".")[:2]) for m in bq_models for ref in m.get("bigquery_refs_explicit", []) + m.get("bigquery_refs_native", []) if ref.count(".") == 2)
    return {
        "mode": pbi.get("mode"), "workspaces": len(pbi.get("workspaces") or []), "reports": len(reports),
        "dashboards": len(pbi.get("dashboards") or []), "dataflows": len(pbi.get("dataflows") or []),
        "semantic_models": len(models), "capacities": refresh.get("capacities") or [], "gateways": len(refresh.get("gateways") or []),
        "datasources_by_type": dict(Counter(d.get("type") for d in pbi.get("datasources") or [])),
        "storage_modes": dict(modes), "models_using_bigquery": len(bq_models),
        "reports_on_bigquery": sum(1 for r in reports if any(m["id"] == r.get("dataset_id") for m in bq_models)),
        "bigquery_projects_used": dict(projects_used.most_common(20)), "bigquery_datasets_used": dict(datasets_used.most_common(30)),
        "native_sql_models": sum(1 for m in models if m.get("native_query_count")),
        "measures": sum(m.get("measure_count") or 0 for m in models), "calculated_columns": sum(m.get("calculated_columns") or 0 for m in models),
        "calculated_tables": sum(m.get("calculated_tables") or 0 for m in models),
        "rls_models": sum(1 for m in models if m.get("rls")), "dynamic_rls_models": sum(1 for m in models if any(r.get("dynamic") for r in m.get("rls_roles") or [])),
        "refresh": {"history_entries": len(hist), "failures": len(fails), "failure_rate": round(len(fails) / len(hist), 3) if hist else None,
                    "p50_duration_s": durs[len(durs) // 2] if durs else None, "max_duration_s": durs[-1] if durs else None,
                    "refreshables_tracked": len(refresh.get("refreshables") or []),
                    "scheduled_models": sum(1 for s in refresh.get("refresh_schedules") or [] if s.get("enabled")) +
                    sum(1 for r in refresh.get("refreshables") or [] if r.get("schedule_enabled"))},
        "active_viewers": (pbi.get("activity") or {}).get("distinct_viewers"),
        "activity_window_days": (pbi.get("activity") or {}).get("window_days"),
        "views_by_hour_utc": (pbi.get("activity") or {}).get("views_by_hour_utc"),
        "refresh_bottlenecks": bottlenecks, "simplification_candidates": simplify, "push_down_candidates": push_down,
        "redundant_model_groups": redundant_models, "redundant_report_groups": redundant_reports[:30],
        "reports_without_views": unused[:50],
        "correlations": [{"model": m["name"], **c} for m in models for c in m.get("correlation") or []],
        "offline_warnings": pbi.get("offline_warnings") or [],
    }
