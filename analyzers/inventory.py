"""Consolida os dados brutos (output/raw) em um inventário normalizado.

Funciona offline (--analyze-only): lê apenas arquivos JSON locais.
Regras de volumetria (sem dupla contagem):
  - volume lógico "migrável" = BASE TABLE + MATERIALIZED VIEW (deleted = false);
  - SNAPSHOT e CLONE contam somente bytes físicos próprios (compartilham storage com a base);
  - VIEW e EXTERNAL não têm armazenamento BigQuery (EXTERNAL é contabilizado no GCS);
  - total_rows vem de TABLE_STORAGE (metadado), nunca de COUNT(*).
"""
from __future__ import annotations

import json
from collections import defaultdict

from analyzers.sql_complexity import analyze_features, analyze_text, features_from_row
from collectors.bigquery_workloads import label_value
from core.context import Context

MIGRATABLE_TYPES = {"BASE TABLE", "MATERIALIZED VIEW"}


def _glob(ctx: Context, module: str, prefix: str) -> list[dict]:
    d = ctx.raw_dir / module
    if not d.exists():
        return []
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(d.glob(f"{prefix}*.json"))]


def build_inventory(ctx: Context) -> dict:
    inv: dict = {"projects": ctx.load_raw("discovery", "projects") or [], "datasets": [], "tables_by_ref": {},
                 "views": [], "routines": [], "column_types": defaultdict(int), "sensitive_columns": [],
                 "policy_tag_columns": [], "row_access_policies": [], "workloads": [], "job_edges": [],
                 "labeled_writes": [], "tables_read_refs": set(), "sql_mode": None, "partitions_daily": []}
    meta_files = _glob(ctx, "bigquery", "metadata__")
    for m in meta_files:
        pid, loc = m["project_id"], m["location"]
        opts = defaultdict(dict)
        for o in m.get("schemata_options") or []:
            opts[o["schema_name"]][o["option_name"]] = o["option_value"]
        for s in m.get("schemata") or []:
            inv["datasets"].append({"ref": f"{pid}.{s['schema_name']}", "project_id": pid, "dataset": s["schema_name"],
                                    "location": s.get("location") or loc, "created": s.get("creation_time"),
                                    "modified": s.get("last_modified_time"), "options": opts.get(s["schema_name"], {}),
                                    "hidden": s["schema_name"].startswith("_")})
        tables = inv["tables_by_ref"]
        for t in m.get("tables") or []:
            ref = f"{pid}.{t['table_schema']}.{t['table_name']}"
            base = ".".join(x for x in [t.get("base_table_catalog"), t.get("base_table_schema"), t.get("base_table_name")] if x)
            tables[ref] = {"ref": ref, "project_id": pid, "dataset": t["table_schema"], "table": t["table_name"],
                           "location": loc, "table_type": t.get("table_type"), "created": t.get("creation_time"),
                           "base_table": base or None, "ddl_available": bool(t.get("ddl"))}
        for s in m.get("table_storage") or []:
            ref = f"{pid}.{s['table_schema']}.{s['table_name']}"
            rec = tables.setdefault(ref, {"ref": ref, "project_id": pid, "dataset": s["table_schema"], "table": s["table_name"],
                                          "location": loc, "table_type": s.get("table_type")})
            for k in ("total_rows", "total_partitions", "total_logical_bytes", "active_logical_bytes", "long_term_logical_bytes",
                      "current_physical_bytes", "total_physical_bytes", "active_physical_bytes", "long_term_physical_bytes",
                      "time_travel_physical_bytes", "fail_safe_physical_bytes"):
                rec[k] = s.get(k)
            rec["deleted"] = bool(s.get("deleted"))
            rec["storage_last_modified"] = s.get("storage_last_modified_time")
            rec.setdefault("table_type", s.get("table_type"))
            if not rec.get("table_type") and s.get("table_type"):
                rec["table_type"] = s["table_type"]
        for c in m.get("columns_agg") or []:
            ref = f"{pid}.{c['table_schema']}.{c['table_name']}"
            rec = tables.get(ref)
            if rec is not None:
                rec.update({"column_count": c.get("column_count"), "partition_columns": c.get("partition_columns"),
                            "clustering_columns": c.get("clustering_columns"), "struct_columns": c.get("struct_columns"),
                            "array_columns": c.get("array_columns"), "json_columns": c.get("json_columns"),
                            "geography_columns": c.get("geography_columns")})
        for o in m.get("table_options") or []:
            ref = f"{pid}.{o['table_schema']}.{o['table_name']}"
            rec = tables.get(ref)
            if rec is None:
                continue
            name, val = o["option_name"], o.get("option_value")
            if name == "uris":
                try:
                    rec["external_uris"] = json.loads(val.replace("'", '"')) if val and val.startswith("[") else [val]
                except (ValueError, AttributeError):
                    rec["external_uris"] = [val]
            else:
                rec.setdefault("options", {})[name] = val
        for ct in m.get("column_types") or []:
            inv["column_types"][ct.get("base_type") or "?"] += ct.get("columns") or 0
        for sc in m.get("sensitive_name_columns") or []:
            inv["sensitive_columns"].append({"table_ref": f"{pid}.{sc['table_schema']}.{sc['table_name']}",
                                             "column": sc["column_name"], "pattern": sc.get("matched_pattern")})
        for pt in m.get("policy_tag_columns") or []:
            inv["policy_tag_columns"].append({"table_ref": f"{pid}.{pt['table_schema']}.{pt['table_name']}",
                                              "column": pt["column_name"], "tags": len(pt.get("policy_tags") or [])})
        dl = m.get("dataset_level") or {}
        for rap in dl.get("row_access_policies") or []:
            inv["row_access_policies"].append({"table_ref": f"{pid}.{rap['table_schema']}.{rap['table_name']}",
                                               "policy": rap.get("row_access_policy_name")})
        for p in dl.get("partitions_daily") or []:
            inv["partitions_daily"].append({**p, "project_id": pid})
        for v in m.get("views") or []:
            ref = f"{pid}.{v['table_schema']}.{v['table_name']}"
            if "view_definition" in v:
                a = analyze_text(v.get("view_definition") or "")
                if not ctx.config.get("sql", {}).get("store_sql_text"):
                    v.pop("view_definition", None)
            elif "f_chars" in v:
                a = analyze_features(features_from_row(v))
            else:
                a = None
            inv["views"].append({"ref": ref, "project_id": pid, "type": "VIEW", "analysis": a,
                                 "referenced_tables": (a or {}).get("referenced_tables", [])})
        for mv in m.get("materialized_views") or []:
            ref = f"{pid}.{mv['table_schema']}.{mv['table_name']}"
            if ref in tables:
                tables[ref]["mv_last_refresh"] = mv.get("last_refresh_time")
        for r in m.get("routines") or []:
            if "routine_definition" in r:
                a = analyze_text(r.get("routine_definition") or "")
                if not ctx.config.get("sql", {}).get("store_sql_text"):
                    r.pop("routine_definition", None)
                    r.pop("ddl", None)
            elif "f_chars" in r:
                a = analyze_features(features_from_row(r))
            else:
                a = None
            lang = (r.get("external_language") or "").upper()
            inv["routines"].append({"ref": f"{pid}.{r['routine_schema']}.{r['routine_name']}", "project_id": pid,
                                    "type": r.get("routine_type"), "body": r.get("routine_body"), "language": lang or "SQL",
                                    "remote": bool(r.get("connection")), "created": r.get("created"),
                                    "last_altered": r.get("last_altered"), "analysis": a})

    # -------------------------------------------------------- workloads
    for w in _glob(ctx, "bigquery_workloads", "workloads__"):
        inv["workloads"].append(w)
        for t in w.get("tables_read") or []:
            ref = t["table_ref"]
            inv["tables_read_refs"].add(ref)
            rec = inv["tables_by_ref"].get(ref)
            if rec is not None:
                rec["reads_jobs"] = (rec.get("reads_jobs") or 0) + (t.get("jobs") or 0)
                rec["readers"] = max(rec.get("readers") or 0, t.get("principals") or 0)
                rec["last_read"] = max(rec.get("last_read") or "", t.get("last_read") or "") or None
                rec["bi_readers"] = (rec.get("bi_readers") or 0) + (t.get("bi_principals") or 0)
                rec["powerbi_readers"] = (rec.get("powerbi_readers") or 0) + (t.get("powerbi_principals") or 0)
        for wr in w.get("writes") or []:
            rec = inv["tables_by_ref"].get(wr["table_ref"])
            if rec is not None:
                rec["write_jobs"] = (rec.get("write_jobs") or 0) + (wr.get("jobs") or 0)
                rec["last_write"] = max(rec.get("last_write") or "", wr.get("last_write") or "") or None
                rec.setdefault("write_types", set()).add(wr.get("statement_type") or wr.get("job_type"))
                rec["write_active_days"] = max(rec.get("write_active_days") or 0, wr.get("active_days") or 0)
            for key, tech in (("airflow-dag", "Cloud Composer (Airflow)"), ("dataform_repository_id", "Dataform"),
                              ("dbt_invocation_id", "dbt"), ("data_source_id", "BigQuery DTS")):
                val = label_value(wr.get("label_kv") or [], key)
                if val:
                    inv["labeled_writes"].append({"technology": tech, "name": val if key != "dbt_invocation_id" else "dbt",
                                                  "table_ref": wr["table_ref"], "label": key, "jobs": wr.get("jobs")})
                    break
        inv["job_edges"].extend(w.get("edges") or [])
    for rec in inv["tables_by_ref"].values():
        if isinstance(rec.get("write_types"), set):
            rec["write_types"] = sorted(x for x in rec["write_types"] if x)

    inv["pipelines"] = ctx.load_raw("pipelines", "pipeline_inventory") or []
    inv["dataform_edges"] = ctx.load_raw("pipelines", "dataform_edges") or []
    inv["lineage_api_links"] = ctx.load_raw("lineage", "lineage_api_links") or []
    inv["transfers"] = ctx.load_raw("pipelines", "transfers") or {}
    inv["pubsub"] = ctx.load_raw("pipelines", "pubsub") or {}
    inv["composer"] = ctx.load_raw("pipelines", "composer") or {}
    inv["datastream"] = ctx.load_raw("pipelines", "datastream") or {}
    inv["dataproc"] = ctx.load_raw("pipelines", "dataproc") or {}
    inv["scheduler"] = ctx.load_raw("pipelines", "scheduler") or {}
    inv["workflows"] = ctx.load_raw("pipelines", "workflows") or {}
    inv["dataform"] = ctx.load_raw("pipelines", "dataform") or {}
    inv["functions_run"] = ctx.load_raw("pipelines", "functions_run") or {}
    inv["datafusion"] = ctx.load_raw("pipelines", "datafusion") or {}
    inv["dataflow"] = ctx.load_raw("pipelines", "dataflow") or {}
    inv["bigquery_connections"] = ctx.load_raw("pipelines", "bigquery_connections") or {}
    inv["services"] = ctx.load_raw("services", "services") or {}
    inv["buckets"] = ctx.load_raw("cloud_storage", "buckets") or []
    inv["gcs_monitoring"] = ctx.load_raw("cloud_storage", "monitoring") or {}
    inv["storage_history"] = ctx.load_raw("bigquery", "storage_history") or {}
    inv["capacity"] = ctx.load_raw("bigquery_capacity", "capacity") or []
    inv["billing"] = ctx.load_raw("billing", "billing_export") or {}
    inv["security"] = ctx.load_raw("security", "security") or {}
    inv["discovery"] = {k: ctx.load_raw("discovery", k) for k in ("organizations", "folders", "services_enabled", "assets",
                                                                     "billing_info", "billing_accounts", "service_accounts",
                                                                     "networks", "api_usage")}
    inv["powerbi"] = ctx.load_raw("powerbi", "powerbi") or {}
    inv["sql_mode"] = (ctx.config.get("sql", {}) or {}).get("mode")
    _powerbi_links(ctx, inv)
    return inv


def _powerbi_links(ctx: Context, inv: dict) -> None:
    """Correlação Power BI x BigQuery com nível de confiança (inferências não viram dependências confirmadas)."""
    pbi = inv["powerbi"]
    tables = inv["tables_by_ref"]
    views = (pbi.get("activity") or {}).get("views_by_report") or {}
    refresh_hours = defaultdict(set)
    for h in (pbi.get("refresh") or {}).get("refresh_history") or []:
        if h.get("start"):
            refresh_hours[h["dataset_id"]].add(h["start"][:13])
    bi_hours = defaultdict(set)   # tabela -> horas em que principals Power BI a leram
    for w in inv["workloads"]:
        for r in w.get("bi_hourly") or []:
            if r.get("matches_powerbi"):
                for ref in r.get("refs") or []:
                    bi_hours[ref].add((r.get("hour") or "")[:13])
    inv["pbi_models"], inv["pbi_reports"], inv["pbi_dashboards"] = [], [], pbi.get("dashboards") or []
    for m in pbi.get("semantic_models") or []:
        explicit = set()
        for t in m.get("tables") or []:
            explicit |= set(t.get("bigquery_refs") or [])
        native = set(m.get("bigquery_refs") or []) - explicit
        explicit = {_resolve(r, tables) for r in explicit}
        native = {_resolve(r, tables) for r in native}
        correlated = []
        for ref in sorted(explicit | native):
            reads = tables.get(ref, {}).get("powerbi_readers") or 0
            overlap = len(refresh_hours.get(m["id"], set()) & bi_hours.get(ref, set()))
            conf = "high" if ref in explicit and (reads or overlap) else "medium" if ref in explicit or overlap else "low"
            correlated.append({"table_ref": ref, "confidence": conf, "powerbi_principal_reads": reads,
                               "refresh_hour_overlap": overlap, "declared": "explícita" if ref in explicit else "SQL nativo"})
        m["bigquery_refs_explicit"] = sorted(explicit)
        m["bigquery_refs_native"] = sorted(native)
        m["correlation"] = correlated
        m["correlated_tables"] = []  # somente inferências sem declaração entrariam aqui (não usadas por padrão)
        m["other_sources"] = sorted(set(m.get("source_types") or []) - {"bigquery"})
        inv["pbi_models"].append(m)
    for r in pbi.get("reports") or []:
        inv["pbi_reports"].append({**r, "views": views.get(r["id"])})


def _resolve(ref: str, tables: dict) -> str:
    """Completa referências parciais (dataset.tabela) quando há correspondência única no inventário."""
    ref = ref.strip("`").replace(":", ".")
    if ref.count(".") == 2 or not ref:
        return ref
    matches = [k for k in tables if k.endswith("." + ref)]
    return matches[0] if len(matches) == 1 else ref
