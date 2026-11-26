"""Exportação CSV (UTF-8 com BOM para abrir corretamente no Excel)."""
from __future__ import annotations

import csv
import json
from pathlib import Path


def _cell(v):
    if isinstance(v, (list, tuple, set)):
        return "; ".join(str(_cell(x)) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, default=str)
    return v


def write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> Path:
    rows = rows or []
    if columns is None:
        columns = []
        for r in rows:
            for k in r:
                if k not in columns and not k.startswith("_"):
                    columns.append(k)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=columns or ["(sem dados)"], extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: _cell(r.get(k)) for k in columns})
    return path


def tables_for_export(a: dict) -> dict[str, tuple[list[dict], list[str] | None]]:
    """Mapeia cada CSV entregável para (linhas, colunas)."""
    inv = a["inventory"]
    assets = [{"project_id": pid, **x} for pid, items in ((inv.get("discovery") or {}).get("assets") or {}).items() for x in items or []]
    vol_by_ds = {d["dataset"]: d for d in a["volume"].get("by_dataset") or []}
    datasets = [{**d, **{k: vol_by_ds.get(d["ref"], {}).get(k) for k in ("logical_bytes", "physical_bytes", "long_term_bytes", "tables", "rows")}}
                for d in inv.get("datasets") or []]
    tables = list(inv.get("tables_by_ref", {}).values())
    cap_rows = []
    for c in inv.get("capacity") or []:
        for r in c.get("reservations") or []:
            cap_rows.append({"type": "reservation", "project_id": c["project_id"], "location": c["location"], **r})
        for r in c.get("commitments") or []:
            cap_rows.append({"type": "commitment", "project_id": c["project_id"], "location": c["location"], **r})
        for r in c.get("assignments") or []:
            cap_rows.append({"type": "assignment", "project_id": c["project_id"], "location": c["location"], **r})
        if c.get("bi_engine") and c["bi_engine"].get("size_bytes"):
            cap_rows.append({"type": "bi_engine", "project_id": c["project_id"], "location": c["location"], **c["bi_engine"]})
    for w in inv.get("workloads") or []:
        for h in w.get("timeline") or []:
            cap_rows.append({"type": "slot_timeline_hour", "project_id": w["project_id"], "location": w["location"], **h})
    pbi = inv.get("powerbi") or {}
    models = [{k: v for k, v in m.items() if k not in ("tables", "measures", "relationships", "correlation")} for m in inv.get("pbi_models") or []]
    deps = [{"model": c["model"], "model_id": next((m["id"] for m in inv.get("pbi_models") or [] if m["name"] == c["model"]), None),
             "dependency_type": "bigquery_table", **{k: v for k, v in c.items() if k != "model"}} for c in a["powerbi"].get("correlations") or []]
    deps += [{"model_id": r.get("dataset_id"), "dependency_type": "report", "report": r.get("name"), "report_id": r.get("id"),
              "confidence": "high", "declared": "Scanner report.datasetId"} for r in pbi.get("reports") or []]
    for m in inv.get("pbi_models") or []:
        for st in m.get("other_sources") or []:
            deps.append({"model": m["name"], "model_id": m["id"], "dependency_type": "external_source", "table_ref": st,
                         "confidence": "high", "declared": "conector Power Query"})
    refresh = list((pbi.get("refresh") or {}).get("refresh_history") or [])
    refresh += [{"dataset_id": r["dataset_id"], "type": "refreshables (agregado)", **r} for r in (pbi.get("refresh") or {}).get("refreshables") or []]
    cost = a["cost"]
    cost_rows = []
    if cost.get("billed"):
        for e in cost["billed"]["evolution"]:
            cost_rows.append({"kind": "billed", "dimension": "total", "key": "total", **e})
        for s in cost["billed"]["by_service_last_month"]:
            cost_rows.append({"kind": "billed", "dimension": "service", "key": s["service"], "month": cost["billed"]["last_full_month"], "net_cost": s["net_cost"]})
        for s in cost["billed"]["by_project_last_month"]:
            cost_rows.append({"kind": "billed", "dimension": "project", "key": s["project_id"], "month": cost["billed"]["last_full_month"], "net_cost": s["net_cost"]})
        for s in cost["billed"]["top_cost_drivers"]:
            cost_rows.append({"kind": "billed", "dimension": "sku", "key": f"{s['service']} / {s['sku']}", "net_cost": s["net_cost_window"], "category": s["category"]})
    for k, v in (cost.get("estimated") or {}).items():
        if isinstance(v, (int, float)):
            cost_rows.append({"kind": "estimated", "dimension": "estimate", "key": k, "net_cost": v, "month": "mensal (estimativa)"})
    for k, v in (cost.get("metrics") or {}).items():
        cost_rows.append({"kind": "metric", "dimension": "consumption", "key": k, "net_cost": None, "value": v})
    return {
        "infrastructure_inventory.csv": (assets or a["footprint"]["services"], None),
        "bigquery_datasets.csv": (datasets, None),
        "bigquery_tables.csv": (tables, ["ref", "project_id", "dataset", "table", "location", "table_type", "created", "storage_last_modified",
                                         "total_rows", "total_logical_bytes", "active_logical_bytes", "long_term_logical_bytes",
                                         "total_physical_bytes", "time_travel_physical_bytes", "column_count", "partition_columns",
                                         "clustering_columns", "partitioned", "clustered", "base_table", "external_uris", "options",
                                         "reads_jobs", "readers", "last_read", "bi_readers", "powerbi_readers", "write_jobs", "last_write",
                                         "write_types", "days_since_modified", "deleted"]),
        "bigquery_routines.csv": ([{**r, "complexity": (r.get("analysis") or {}).get("complexity"), "score": (r.get("analysis") or {}).get("score"),
                                    "anti_patterns": (r.get("analysis") or {}).get("anti_patterns")} for r in inv.get("routines") or []],
                                  ["ref", "project_id", "type", "body", "language", "remote", "created", "last_altered", "complexity", "score", "anti_patterns"]),
        "workload_analysis.csv": (a["workloads"].get("consumers") or [], ["principal", "projects", "workload_class", "class_confidence",
                                                                          "class_reason", "is_service_account", "principal_domain", "jobs", "query_jobs",
                                                                          "load_jobs", "write_statements", "select_statements", "bytes_billed", "slot_ms",
                                                                          "distinct_fingerprints", "cache_hits", "active_days", "active_hours", "matches_powerbi"]),
        "query_complexity.csv": (a["sql"].get("objects") or [], None),
        "capacity_analysis.csv": (cap_rows, None),
        "pipeline_inventory.csv": (inv.get("pipelines") or [], None),
        "cloud_storage_inventory.csv": ([{k: v for k, v in b.items() if k != "sample"} | {f"sample_{k}": v for k, v in (b.get("sample") or {}).items()}
                                         for b in inv.get("buckets") or []], None),
        "powerbi_inventory.csv": (models, None),
        "powerbi_dependencies.csv": (deps, None),
        "powerbi_refresh_history.csv": (refresh, None),
        "cost_analysis.csv": (cost_rows, None),
        "security_findings.csv": (a["security"].get("findings") or [], None),
    }
