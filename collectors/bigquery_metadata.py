"""BigQuery — inventário estrutural e volumetria (itens 4 e 5).

Para cada (projeto, região):
  1. valida disponibilidade/campos de cada visão INFORMATION_SCHEMA com dry run
     (`SELECT *` sem custo) e monta as consultas apenas com colunas existentes;
  2. executa consultas agregadas (nunca lê tabelas de negócio; nunca usa COUNT(*)
     em tabelas de negócio — contagem de linhas vem de TABLE_STORAGE.total_rows).
Histórico de volume: Cloud Monitoring `bigquery.googleapis.com/storage/stored_bytes`
(somente o período efetivamente retornado pela API; nada é extrapolado).
"""
from __future__ import annotations

import re

from datetime import datetime, timedelta, timezone

from analyzers.sql_complexity import server_feature_select
from core.context import Context
from utils.bq import ColumnSelector, CostLimitExceeded, dataset_info_schema, info_schema
from utils.http import ApiError
from utils.logging import get_logger

log = get_logger("bq_meta")
MODULE = "bigquery"
MON = "https://monitoring.googleapis.com/v3"

REGION_VIEWS = ["SCHEMATA", "SCHEMATA_OPTIONS", "TABLES", "TABLE_OPTIONS", "TABLE_STORAGE", "COLUMNS",
                "COLUMN_FIELD_PATHS", "VIEWS", "MATERIALIZED_VIEWS", "ROUTINES"]
TABLE_OPTION_NAMES = ["expiration_timestamp", "partition_expiration_days", "require_partition_filter", "kms_key_name",
                      "uris", "format", "max_staleness", "enable_refresh", "refresh_interval_minutes", "labels",
                      "table_format", "storage_uri", "file_format"]
SCHEMA_OPTION_NAMES = ["default_table_expiration_days", "default_partition_expiration_days", "max_time_travel_hours",
                       "storage_billing_model", "default_kms_key_name", "is_case_insensitive", "labels"]



def sensitive_regex(patterns: list[str]) -> str:
    """Regex RE2 para nomes de coluna sensíveis. REGEXP_EXTRACT aceita no máximo 1 grupo de captura:
    só o grupo do padrão captura; os demais são não capturantes."""
    return "(?:^|_)(" + "|".join(re.escape(p.lower()) for p in patterns) + r")(?:_|$|[0-9])"

def _q(ctx: Context, project: str, location: str, sql: str, name: str, component: str, max_rows: int = 200_000):
    try:
        rows = ctx.bq.query(project, location, sql, name=f"{name}:{project}:{location}", max_rows=max_rows)
        ctx.coverage.ok(MODULE, component, len(rows))
        return rows
    except CostLimitExceeded as exc:
        ctx.coverage.limitation(MODULE, component, str(exc))
        ctx.coverage.entry(MODULE, component).scope_errors += 1
        return None
    except ApiError as exc:
        ctx.coverage.fail(MODULE, component, exc, scope=f"{project}/{location}")
        return None


def _sql_mode(ctx: Context) -> str:
    mode = (ctx.config.get("sql", {}).get("mode") or "features").lower()
    if mode == "full" and not ctx.include_sql:
        ctx.coverage.limitation(MODULE, "sql_definitions", "sql.mode=full requer --include-sql; usando 'features'.")
        return "features"
    return mode


def collect(ctx: Context) -> dict:
    cfg = ctx.config["bigquery"]
    mode = _sql_mode(ctx)
    targets = [(pid, loc) for pid in ctx.project_ids() for loc in ctx.regions_for(pid)]
    out: dict = {"view_availability": {}, "sql_mode": mode}

    def per_region(target):
        pid, loc = target
        key = f"{pid}__{loc}"
        schemas = {v: ctx.bq.view_schema(pid, loc, info_schema(pid, loc, v)) for v in REGION_VIEWS}
        out["view_availability"][key] = {v: (sorted(s) if s else None) for v, s in schemas.items()}
        res: dict = {"project_id": pid, "location": loc}
        sel = {v: ColumnSelector(s) for v, s in schemas.items()}

        if schemas["SCHEMATA"]:
            res["schemata"] = _q(ctx, pid, loc, f"""
SELECT schema_name, {sel['SCHEMATA'].col('location')}, {sel['SCHEMATA'].col('creation_time', 'TIMESTAMP')},
  {sel['SCHEMATA'].col('last_modified_time', 'TIMESTAMP')}
FROM {info_schema(pid, loc, 'SCHEMATA')}""", "schemata", "datasets")
        if schemas["SCHEMATA_OPTIONS"]:
            names = ", ".join(f"'{n}'" for n in SCHEMA_OPTION_NAMES)
            res["schemata_options"] = _q(ctx, pid, loc, f"""
SELECT schema_name, option_name, option_value FROM {info_schema(pid, loc, 'SCHEMATA_OPTIONS')}
WHERE option_name IN ({names})""", "schemata_options", "datasets")
        if schemas["TABLES"]:
            s = sel["TABLES"]
            ddl = f", {s.col('ddl')}" if mode == "full" else ""
            res["tables"] = _q(ctx, pid, loc, f"""
SELECT table_schema, table_name, table_type, {s.col('creation_time', 'TIMESTAMP')},
  {s.col('base_table_catalog')}, {s.col('base_table_schema')}, {s.col('base_table_name')},
  {s.col('snapshot_time_ms', 'TIMESTAMP')}, {s.col('is_insertable_into')}{ddl}
FROM {info_schema(pid, loc, 'TABLES')}""", "tables", "tables")
        if schemas["TABLE_OPTIONS"]:
            names = ", ".join(f"'{n}'" for n in TABLE_OPTION_NAMES)
            res["table_options"] = _q(ctx, pid, loc, f"""
SELECT table_schema, table_name, option_name, option_value FROM {info_schema(pid, loc, 'TABLE_OPTIONS')}
WHERE option_name IN ({names})""", "table_options", "tables")
        if schemas["TABLE_STORAGE"]:
            s = sel["TABLE_STORAGE"]
            cols = ", ".join(s.col(c, "INT64") for c in [
                "total_rows", "total_partitions", "total_logical_bytes", "active_logical_bytes", "long_term_logical_bytes",
                "current_physical_bytes", "total_physical_bytes", "active_physical_bytes", "long_term_physical_bytes",
                "time_travel_physical_bytes", "fail_safe_physical_bytes"])
            res["table_storage"] = _q(ctx, pid, loc, f"""
SELECT table_schema, table_name, {s.col('table_type')}, {s.col('creation_time', 'TIMESTAMP')},
  {s.col('storage_last_modified_time', 'TIMESTAMP')}, {s.col('deleted', 'BOOL')}, {cols}
FROM {info_schema(pid, loc, 'TABLE_STORAGE')}""", "table_storage", "volumetry")
        else:
            ctx.coverage.limitation(MODULE, "volumetry", f"{pid}/{loc}: TABLE_STORAGE indisponível")
        if schemas["COLUMNS"] and cfg.get("collect_columns", True):
            s = sel["COLUMNS"]
            res["columns_agg"] = _q(ctx, pid, loc, f"""
SELECT table_schema, table_name, COUNT(1) AS column_count,
  STRING_AGG(IF({s.expr('is_partitioning_column')} = 'YES', column_name, NULL)) AS partition_columns,
  STRING_AGG(IF({s.expr('clustering_ordinal_position', 'INT64')} IS NOT NULL, column_name, NULL), ','
             ORDER BY {s.expr('clustering_ordinal_position', 'INT64')}) AS clustering_columns,
  COUNTIF(STARTS_WITH(data_type, 'STRUCT')) AS struct_columns,
  COUNTIF(STARTS_WITH(data_type, 'ARRAY')) AS array_columns,
  COUNTIF(data_type = 'JSON') AS json_columns,
  COUNTIF(data_type = 'GEOGRAPHY') AS geography_columns
FROM {info_schema(pid, loc, 'COLUMNS')} GROUP BY 1, 2""", "columns_agg", "columns")
            res["column_types"] = _q(ctx, pid, loc, f"""
SELECT REGEXP_EXTRACT(data_type, r'^[A-Z0-9_]+') AS base_type, COUNT(1) AS columns
FROM {info_schema(pid, loc, 'COLUMNS')} GROUP BY 1""", "column_types", "columns")
            pats = [p.lower() for p in cfg.get("sensitive_column_patterns") or []]
            if pats:
                rx = sensitive_regex(pats)
                res["sensitive_name_columns"] = _q(ctx, pid, loc, f"""
SELECT table_schema, table_name, column_name, REGEXP_EXTRACT(LOWER(column_name), r'{rx}') AS matched_pattern
FROM {info_schema(pid, loc, 'COLUMNS')} WHERE REGEXP_CONTAINS(LOWER(column_name), r'{rx}')""",
                    "sensitive_name_columns", "columns", max_rows=50_000)
        if schemas["COLUMN_FIELD_PATHS"] and ColumnSelector(schemas["COLUMN_FIELD_PATHS"]).has("policy_tags"):
            res["policy_tag_columns"] = _q(ctx, pid, loc, f"""
SELECT table_schema, table_name, column_name, field_path, policy_tags
FROM {info_schema(pid, loc, 'COLUMN_FIELD_PATHS')} WHERE ARRAY_LENGTH(policy_tags) > 0""",
                "policy_tag_columns", "security_metadata")
        if schemas["VIEWS"] and mode != "none":
            body = server_feature_select("view_definition") if mode == "features" else "view_definition"
            res["views"] = _q(ctx, pid, loc, f"""
SELECT table_schema, table_name, {sel['VIEWS'].col('use_standard_sql')}, {body}
FROM {info_schema(pid, loc, 'VIEWS')}""", "views", "views")
        elif schemas["VIEWS"]:
            res["views"] = _q(ctx, pid, loc, f"SELECT table_schema, table_name FROM {info_schema(pid, loc, 'VIEWS')}",
                              "views", "views")
        if schemas["MATERIALIZED_VIEWS"]:
            s = sel["MATERIALIZED_VIEWS"]
            res["materialized_views"] = _q(ctx, pid, loc, f"""
SELECT table_schema, table_name, {s.col('last_refresh_time', 'TIMESTAMP')}, {s.col('refresh_watermark', 'TIMESTAMP')}
FROM {info_schema(pid, loc, 'MATERIALIZED_VIEWS')}""", "materialized_views", "views")
        if schemas["ROUTINES"]:
            s = sel["ROUTINES"]
            if mode == "features":
                body = ", " + server_feature_select("routine_definition")
            elif mode == "full":
                body = f", {s.col('routine_definition')}, {s.col('ddl')}"
            else:
                body = ""
            res["routines"] = _q(ctx, pid, loc, f"""
SELECT routine_schema, routine_name, routine_type, {s.col('routine_body')}, {s.col('external_language')},
  {s.col('data_type')}, {s.col('created', 'TIMESTAMP')}, {s.col('last_altered', 'TIMESTAMP')},
  {s.col('connection')}, {s.col('security_type')}{body}
FROM {info_schema(pid, loc, 'ROUTINES')}""", "routines", "routines")

        res["dataset_level"] = _dataset_level(ctx, pid, loc, res.get("schemata") or [], mode)
        ctx.save_raw(MODULE, f"metadata__{key}", res)
        return key

    ctx.run_parallel(per_region, targets, "bigquery_metadata")
    ctx.save_raw(MODULE, "view_availability", out["view_availability"])
    storage_history = _storage_history(ctx)
    ctx.save_raw(MODULE, "storage_history", storage_history)

    window = f"{min(ctx.days, 180)} dias (Cloud Monitoring)"
    for comp, src in [("datasets", "INFORMATION_SCHEMA.SCHEMATA/SCHEMATA_OPTIONS"),
                      ("tables", "INFORMATION_SCHEMA.TABLES/TABLE_OPTIONS"),
                      ("volumetry", "INFORMATION_SCHEMA.TABLE_STORAGE"),
                      ("columns", "INFORMATION_SCHEMA.COLUMNS"),
                      ("views", "INFORMATION_SCHEMA.VIEWS/MATERIALIZED_VIEWS"),
                      ("routines", "INFORMATION_SCHEMA.ROUTINES"),
                      ("security_metadata", "INFORMATION_SCHEMA.COLUMN_FIELD_PATHS / ROW_ACCESS_POLICIES"),
                      ("partitions", "INFORMATION_SCHEMA.PARTITIONS (por dataset)"),
                      ("storage_history", "Cloud Monitoring bigquery.googleapis.com/storage/stored_bytes")]:
        ctx.coverage.finalize(MODULE, comp, source=src, history_window=window if comp == "storage_history" else None)
    if not cfg.get("collect_partitions"):
        ctx.coverage.entry(MODULE, "partitions").status = "Not Assessed"
        ctx.coverage.limitation(MODULE, "partitions", "Desabilitado (bigquery.collect_partitions=false)")
    if mode != "full":
        ctx.coverage.limitation(MODULE, "views", f"sql.mode={mode}: definições SQL não exportadas; "
                                                 "lineage de views depende de jobs/Data Lineage API")
    return {"regions": len(targets), "sql_mode": mode}


def _dataset_level(ctx: Context, pid: str, loc: str, schemata: list[dict], mode: str) -> dict:
    """Visões com qualificador de dataset (ROW_ACCESS_POLICIES e, opcionalmente, PARTITIONS)."""
    cfg = ctx.config["bigquery"]
    cap = int(cfg.get("max_datasets_dataset_level_views", 200))
    datasets = [s["schema_name"] for s in schemata][:cap]
    if len(schemata) > cap:
        ctx.coverage.limitation(MODULE, "security_metadata",
                                f"{pid}/{loc}: ROW_ACCESS_POLICIES avaliado em {cap} de {len(schemata)} datasets")
    out: dict = {"row_access_policies": [], "partitions_by_table": [], "partitions_daily": []}
    for ds in datasets:
        rap = dataset_info_schema(pid, ds, "ROW_ACCESS_POLICIES")
        schema = ctx.bq.view_schema(pid, loc, rap)
        if schema:
            pred = ", filter_predicate" if mode == "full" and "filter_predicate" in schema else ""
            rows = _q(ctx, pid, loc, f"SELECT table_schema, table_name, row_access_policy_name{pred} FROM {rap}",
                      "row_access_policies", "security_metadata")
            out["row_access_policies"].extend(rows or [])
        if cfg.get("collect_partitions"):
            pv = dataset_info_schema(pid, ds, "PARTITIONS")
            if ctx.bq.view_schema(pid, loc, pv):
                since = max(ctx.days, 30)
                rows = _q(ctx, pid, loc, f"""
SELECT table_schema, table_name, COUNT(1) AS partitions,
  COUNTIF(partition_id NOT IN ('__NULL__', '__UNPARTITIONED__', '__STREAMING_UNPARTITIONED__')) AS dated_partitions,
  SUM(total_logical_bytes) AS logical_bytes, MAX(last_modified_time) AS last_modified_time
FROM {pv} GROUP BY 1, 2""", "partitions_by_table", "partitions")
                out["partitions_by_table"].extend(rows or [])
                rows = _q(ctx, pid, loc, f"""
SELECT DATE(last_modified_time) AS day, COUNT(1) AS partitions_modified, SUM(total_logical_bytes) AS logical_bytes,
  SUM(total_rows) AS rows_in_modified_partitions
FROM {pv} WHERE last_modified_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {since} DAY) GROUP BY 1""",
                          "partitions_daily", "partitions")
                for r in rows or []:
                    r["table_schema"] = ds
                out["partitions_daily"].extend(rows or [])
    return out


def _storage_history(ctx: Context) -> dict:
    """Série diária de bytes armazenados e bytes de streaming por projeto (Cloud Monitoring)."""
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    days = min(max(ctx.days, 30), 180)
    start = end - timedelta(days=days)
    result: dict = {}
    metrics = {"stored_bytes": ("bigquery.googleapis.com/storage/stored_bytes", "ALIGN_MEAN"),
               "uploaded_bytes": ("bigquery.googleapis.com/storage/uploaded_bytes", "ALIGN_SUM")}

    def per_project(pid):
        result[pid] = {}
        for name, (metric, aligner) in metrics.items():
            params = {
                "filter": f'metric.type="{metric}" AND resource.type="bigquery_dataset"',
                "interval.startTime": start.isoformat().replace("+00:00", "Z"),
                "interval.endTime": end.isoformat().replace("+00:00", "Z"),
                "aggregation.alignmentPeriod": "86400s",
                "aggregation.perSeriesAligner": aligner,
                "aggregation.crossSeriesReducer": "REDUCE_SUM",
                "view": "FULL",
            }
            try:
                series = {}
                for ts in ctx.gcp.paginate(f"{MON}/projects/{pid}/timeSeries", "timeSeries", params):
                    for pt in ts.get("points") or []:
                        day = (pt.get("interval", {}).get("endTime") or "")[:10]
                        v = pt.get("value") or {}
                        series[day] = series.get(day, 0) + float(v.get("doubleValue") or v.get("int64Value") or 0)
                result[pid][name] = dict(sorted(series.items()))
                ctx.coverage.ok(MODULE, "storage_history", len(series))
            except ApiError as exc:
                ctx.coverage.fail(MODULE, "storage_history", exc, scope=f"{pid}/{name}")

    ctx.run_parallel(per_project, ctx.project_ids(), "storage_history")
    return result
