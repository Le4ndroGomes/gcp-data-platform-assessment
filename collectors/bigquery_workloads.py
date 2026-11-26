"""BigQuery — histórico de jobs e performance (item 6).

Fonte: INFORMATION_SCHEMA.JOBS_BY_PROJECT e JOBS_TIMELINE_BY_PROJECT (retenção
de 180 dias, qualificador de região obrigatório). Todas as agregações são feitas
no BigQuery; apenas resultados agregados são trazidos.

Regras anti-dupla contagem:
  - bytes/slots somam somente jobs com statement_type != 'SCRIPT' (o job pai de
    um script multi-statement já resume os filhos — orientação da documentação);
  - contagem de "jobs lógicos" usa parent_job_id IS NULL (scripts contam 1 vez);
  - durações/percentis consideram apenas jobs de topo (parent_job_id IS NULL).
Principals são pseudonimizados antes de gravar em disco (output.anonymize).
"""
from __future__ import annotations

import re

from analyzers.sql_complexity import server_feature_select
from core.context import Context, matches_any
from utils.bq import ColumnSelector, CostLimitExceeded, info_schema
from utils.http import ApiError
from utils.logging import get_logger

log = get_logger("bq_workloads")
MODULE = "bigquery_workloads"
DURATION_BUCKETS_MS = [100, 250, 500, 1000, 2000, 5000, 10000, 30000, 60000, 120000, 300000, 600000, 1800000, 3600000]
NOT_SCRIPT = "IFNULL(statement_type, '') != 'SCRIPT'"


def _base_cte(s: ColumnSelector, view: str, days: int) -> str:
    fp_norm = r"""TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))"""
    fp = (f"COALESCE({s.expr('query_info.query_hashes.normalized_literals', 'STRING')}, {fp_norm})"
          if s.has("query_info") else fp_norm)
    refs = ("ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) "
            "FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL)") if s.has("referenced_tables") else "CAST([] AS ARRAY<STRING>)"
    labels = ("ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l)"
              if s.has("labels") else "CAST([] AS ARRAY<STRING>)")
    dest = ("IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, "
            "FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id))"
            if s.has("destination_table") else "CAST(NULL AS STRING)")
    return f"""j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL({s.expr('cache_hit', 'BOOL')}, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, {s.col('edition')},
    IFNULL(user_email, {s.expr('principal_subject')}) AS principal,
    {s.col('parent_job_id')},
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    {fp} AS fp, {refs} AS refs, {labels} AS label_kv, {dest} AS dest, query
  FROM {view}
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
)"""


def collect(ctx: Context) -> dict:
    days = min(int(ctx.days), 180)
    if ctx.days > 180:
        ctx.coverage.limitation(MODULE, "jobs", "JOBS retém 180 dias; janela limitada a 180.")
    targets = [(pid, loc) for pid in ctx.project_ids() for loc in ctx.regions_for(pid)]
    sql_mode = (ctx.config.get("sql", {}).get("mode") or "features").lower()
    if sql_mode == "full" and not ctx.include_sql:
        sql_mode = "features"
    top_n = int(ctx.config["bigquery"].get("top_fingerprints", 200))
    top_tables = int(ctx.config["bigquery"].get("top_tables", 500))
    rules = ctx.config.get("workload_rules", {})
    pbi_patterns = list(ctx.config.get("powerbi", {}).get("principal_patterns", [])) + list(rules.get("bi_principals", []))

    def q(pid, loc, sql, name, component, max_rows=200_000):
        try:
            rows = ctx.bq.query(pid, loc, sql, name=f"{name}:{pid}:{loc}", max_rows=max_rows)
            ctx.coverage.ok(MODULE, component, len(rows))
            return rows
        except CostLimitExceeded as exc:
            ctx.coverage.limitation(MODULE, component, str(exc))
            ctx.coverage.entry(MODULE, component).scope_errors += 1
        except ApiError as exc:
            ctx.coverage.fail(MODULE, component, exc, scope=f"{pid}/{loc}")
        return None

    def per_region(target):
        pid, loc = target
        view = info_schema(pid, loc, "JOBS_BY_PROJECT")
        schema = ctx.bq.view_schema(pid, loc, view)
        if not schema:
            ctx.coverage.fail(MODULE, "jobs", ApiError(403, "JOBS_BY_PROJECT inacessível (bigquery.jobs.listAll)",
                                                       "https://bigquery.googleapis.com/", "accessDenied"),
                              scope=f"{pid}/{loc}")
            return
        s = ColumnSelector(schema)
        with_ = "WITH " + _base_cte(s, view, days)
        res: dict = {"project_id": pid, "location": loc, "window_days": days}

        res["hourly"] = q(pid, loc, f"""{with_}
SELECT TIMESTAMP_TRUNC(creation_time, HOUR) AS hour, job_type, priority,
  COUNTIF(parent_job_id IS NULL) AS jobs, COUNTIF({NOT_SCRIPT}) AS statements,
  COUNTIF(statement_type = 'SCRIPT') AS scripts, COUNTIF(parent_job_id IS NOT NULL) AS child_jobs,
  COUNTIF(failed AND parent_job_id IS NULL) AS failed, COUNTIF(cache_hit) AS cache_hits,
  SUM(IF({NOT_SCRIPT}, bytes_processed, 0)) AS bytes_processed,
  SUM(IF({NOT_SCRIPT}, bytes_billed, 0)) AS bytes_billed,
  SUM(IF({NOT_SCRIPT} AND reservation_id IS NULL, bytes_billed, 0)) AS on_demand_bytes_billed,
  SUM(IF({NOT_SCRIPT}, slot_ms, 0)) AS slot_ms,
  SUM(IF(parent_job_id IS NULL, run_ms, 0)) AS sum_run_ms, MAX(IF(parent_job_id IS NULL, run_ms, NULL)) AS max_run_ms,
  SUM(IF(parent_job_id IS NULL, queue_ms, 0)) AS sum_queue_ms,
  COUNT(DISTINCT principal) AS principals
FROM j GROUP BY 1, 2, 3""", "hourly", "jobs")

        res["duration_histogram"] = q(pid, loc, f"""{with_}
SELECT job_type, priority, RANGE_BUCKET(run_ms, {DURATION_BUCKETS_MS}) AS run_bucket,
  RANGE_BUCKET(queue_ms, {DURATION_BUCKETS_MS}) AS queue_bucket, COUNT(1) AS jobs
FROM j WHERE parent_job_id IS NULL AND run_ms IS NOT NULL GROUP BY 1, 2, 3, 4""", "duration_histogram", "performance")

        res["percentiles"] = q(pid, loc, f"""{with_}
SELECT job_type, priority, COUNT(1) AS jobs, AVG(run_ms) AS avg_run_ms,
  APPROX_QUANTILES(run_ms, 100)[OFFSET(50)] AS p50_ms, APPROX_QUANTILES(run_ms, 100)[OFFSET(90)] AS p90_ms,
  APPROX_QUANTILES(run_ms, 100)[OFFSET(95)] AS p95_ms, APPROX_QUANTILES(run_ms, 100)[OFFSET(99)] AS p99_ms,
  AVG(queue_ms) AS avg_queue_ms, APPROX_QUANTILES(queue_ms, 100)[OFFSET(95)] AS p95_queue_ms
FROM j WHERE parent_job_id IS NULL AND run_ms IS NOT NULL GROUP BY 1, 2""", "percentiles", "performance")

        if sql_mode == "features":
            text_cols = ",\n  " + server_feature_select("sample_query")
        elif sql_mode == "full":
            max_chars = int(ctx.config.get("sql", {}).get("max_sql_chars", 100_000))
            text_cols = f",\n  SUBSTR(sample_query, 1, {max_chars}) AS sample_query"
        else:
            text_cols = ""
        res["fingerprints"] = q(pid, loc, f"""{with_},
agg AS (
  SELECT fp, ANY_VALUE(statement_type) AS statement_type, ANY_VALUE(priority) AS priority, COUNT(1) AS runs,
    COUNT(DISTINCT principal) AS principals, ANY_VALUE(principal) AS sample_principal,
    SUM(bytes_billed) AS bytes_billed, SUM(bytes_processed) AS bytes_processed, SUM(slot_ms) AS slot_ms,
    AVG(run_ms) AS avg_run_ms, MAX(run_ms) AS max_run_ms, APPROX_QUANTILES(run_ms, 100)[OFFSET(95)] AS p95_run_ms,
    COUNTIF(failed) AS failed, COUNTIF(cache_hit) AS cache_hits, MIN(creation_time) AS first_seen,
    MAX(creation_time) AS last_seen, COUNT(DISTINCT DATE(creation_time)) AS active_days,
    ARRAY_CONCAT_AGG(refs LIMIT 10) AS refs, ARRAY_CONCAT_AGG(label_kv LIMIT 5) AS label_kv,
    ANY_VALUE(dest) AS sample_destination, ANY_VALUE(query) AS sample_query
  FROM j WHERE job_type = 'QUERY' AND {NOT_SCRIPT} GROUP BY fp
)
SELECT * EXCEPT (sample_query, refs, label_kv){text_cols},
  ARRAY(SELECT x FROM UNNEST(refs) x WHERE x IS NOT NULL) AS refs,
  ARRAY(SELECT x FROM UNNEST(label_kv) x WHERE x IS NOT NULL) AS label_kv
FROM agg WHERE TRUE
QUALIFY RANK() OVER (ORDER BY slot_ms DESC) <= {top_n} OR RANK() OVER (ORDER BY bytes_billed DESC) <= {top_n}
     OR RANK() OVER (ORDER BY runs DESC) <= {top_n} OR RANK() OVER (ORDER BY avg_run_ms DESC) <= {top_n}""",
                                "fingerprints", "query_patterns")

        res["fingerprint_summary"] = q(pid, loc, f"""{with_},
agg AS (SELECT fp, COUNT(1) AS runs, COUNT(DISTINCT DATE(creation_time)) AS days FROM j
        WHERE job_type = 'QUERY' AND {NOT_SCRIPT} GROUP BY fp)
SELECT COUNT(1) AS distinct_fingerprints, COUNTIF(runs > 1) AS recurring_fingerprints,
  COUNTIF(days >= 7) AS fingerprints_7plus_days, SUM(runs) AS query_statements,
  SUM(IF(runs > 1, runs, 0)) AS runs_of_recurring
FROM agg""", "fingerprint_summary", "query_patterns")

        res["principals"] = q(pid, loc, f"""{with_}
SELECT principal, COUNTIF(parent_job_id IS NULL) AS jobs, COUNTIF(job_type = 'QUERY') AS query_jobs,
  COUNTIF(job_type = 'LOAD') AS load_jobs, COUNTIF(job_type IN ('EXTRACT', 'COPY')) AS extract_copy_jobs,
  COUNTIF(statement_type IN ('INSERT', 'UPDATE', 'DELETE', 'MERGE', 'CREATE_TABLE_AS_SELECT', 'CREATE_TABLE',
                             'CREATE_VIEW', 'TRUNCATE_TABLE')) AS write_statements,
  COUNTIF(statement_type IN ('CREATE_MODEL', 'EXPORT_MODEL')) AS ml_statements,
  COUNTIF(statement_type = 'SELECT') AS select_statements,
  SUM(IF({NOT_SCRIPT}, bytes_billed, 0)) AS bytes_billed, SUM(IF({NOT_SCRIPT}, slot_ms, 0)) AS slot_ms,
  COUNT(DISTINCT fp) AS distinct_fingerprints, COUNTIF(cache_hit) AS cache_hits,
  COUNT(DISTINCT EXTRACT(HOUR FROM creation_time)) AS active_hours, COUNT(DISTINCT DATE(creation_time)) AS active_days,
  MIN(creation_time) AS first_seen, MAX(creation_time) AS last_seen
FROM j GROUP BY principal ORDER BY slot_ms DESC LIMIT 5000""", "principals", "consumers")

        res["labels"] = q(pid, loc, f"""{with_}
SELECT kv, principal, COUNT(1) AS jobs, SUM(IF({NOT_SCRIPT}, slot_ms, 0)) AS slot_ms,
  SUM(IF({NOT_SCRIPT}, bytes_billed, 0)) AS bytes_billed, MAX(creation_time) AS last_seen
FROM j, UNNEST(label_kv) kv GROUP BY 1, 2 ORDER BY jobs DESC LIMIT 5000""", "labels", "consumers")

        res["tables_read"] = q(pid, loc, f"""{with_}
SELECT ref AS table_ref, COUNT(1) AS jobs, COUNT(DISTINCT principal) AS principals, MAX(creation_time) AS last_read,
  MIN(creation_time) AS first_read, SUM(bytes_billed) AS attributed_bytes_billed,
  ARRAY_AGG(DISTINCT principal IGNORE NULLS LIMIT 25) AS principal_sample
FROM j, UNNEST(refs) ref WHERE {NOT_SCRIPT} GROUP BY 1 ORDER BY jobs DESC LIMIT {top_tables * 4}""",
                              "tables_read", "table_access")

        res["writes"] = q(pid, loc, f"""{with_}
SELECT dest AS table_ref, job_type, IFNULL(statement_type, '') AS statement_type, COUNT(1) AS jobs,
  SUM(slot_ms) AS slot_ms, SUM(bytes_processed) AS bytes_processed, MAX(creation_time) AS last_write,
  COUNT(DISTINCT DATE(creation_time)) AS active_days, ARRAY_AGG(DISTINCT principal IGNORE NULLS LIMIT 10) AS principals,
  ARRAY_CONCAT_AGG(label_kv LIMIT 3) AS label_kv
FROM j WHERE dest IS NOT NULL AND {NOT_SCRIPT} GROUP BY 1, 2, 3 LIMIT 50000""", "writes", "lineage_edges")

        res["edges"] = q(pid, loc, f"""{with_}
SELECT ref AS source_ref, dest AS target_ref, COUNT(1) AS jobs, MAX(creation_time) AS last_seen,
  ANY_VALUE(statement_type) AS statement_type, COUNT(DISTINCT DATE(creation_time)) AS active_days
FROM j, UNNEST(refs) ref WHERE dest IS NOT NULL AND ref != dest AND {NOT_SCRIPT}
GROUP BY 1, 2 LIMIT 100000""", "edges", "lineage_edges")

        if s.has("edition") or s.has("reservation_id"):
            res["by_reservation"] = q(pid, loc, f"""{with_}
SELECT IFNULL(reservation_id, 'ON_DEMAND') AS reservation_id, IFNULL(edition, '') AS edition,
  COUNT(1) AS jobs, SUM(IF({NOT_SCRIPT}, slot_ms, 0)) AS slot_ms, SUM(IF({NOT_SCRIPT}, bytes_billed, 0)) AS bytes_billed
FROM j GROUP BY 1, 2""", "by_reservation", "capacity_usage")

        if pbi_patterns:
            rx = "(" + "|".join(re.escape(x.lower()) for x in pbi_patterns) + ")"
            res["bi_hourly"] = q(pid, loc, f"""{with_}
SELECT hour, principal, jobs, bytes_billed, slot_ms, ARRAY(SELECT x FROM UNNEST(refs) x WHERE x IS NOT NULL) AS refs
FROM (
  SELECT TIMESTAMP_TRUNC(creation_time, HOUR) AS hour, principal, COUNT(1) AS jobs,
    SUM(IF({NOT_SCRIPT}, bytes_billed, 0)) AS bytes_billed, SUM(IF({NOT_SCRIPT}, slot_ms, 0)) AS slot_ms,
    ARRAY_CONCAT_AGG(refs LIMIT 50) AS refs
  FROM j WHERE REGEXP_CONTAINS(LOWER(IFNULL(principal, '')), r'{rx}') GROUP BY 1, 2 LIMIT 50000)""",
                                 "bi_hourly", "consumers")

        res["timeline"] = _timeline(ctx, pid, loc, days, q)
        _anonymize(ctx, res, pbi_patterns, rules)
        ctx.save_raw(MODULE, f"workloads__{pid}__{loc}", res)

    ctx.run_parallel(per_region, targets, "bigquery_workloads")
    for comp, src in [("jobs", "INFORMATION_SCHEMA.JOBS_BY_PROJECT"), ("performance", "JOBS_BY_PROJECT (durações)"),
                      ("query_patterns", "JOBS_BY_PROJECT (fingerprints)"), ("consumers", "JOBS_BY_PROJECT (principals/labels)"),
                      ("table_access", "JOBS_BY_PROJECT.referenced_tables"), ("lineage_edges", "JOBS_BY_PROJECT destination/referenced"),
                      ("capacity_usage", "JOBS_BY_PROJECT reservation_id/edition"),
                      ("slot_timeline", "INFORMATION_SCHEMA.JOBS_TIMELINE_BY_PROJECT")]:
        ctx.coverage.finalize(MODULE, comp, source=src, history_window=f"{days} dias")
    return {"regions": len(targets), "window_days": days}


def _timeline(ctx: Context, pid: str, loc: str, days: int, q) -> list | None:
    view = info_schema(pid, loc, "JOBS_TIMELINE_BY_PROJECT")
    schema = ctx.bq.view_schema(pid, loc, view)
    if not schema:
        ctx.coverage.fail(MODULE, "slot_timeline",
                          ApiError(403, "JOBS_TIMELINE_BY_PROJECT inacessível", "https://bigquery.googleapis.com/",
                                   "accessDenied"), scope=f"{pid}/{loc}")
        return None
    s = ColumnSelector(schema)
    not_script = f"IFNULL({s.expr('statement_type')}, '') != 'SCRIPT'"
    return q(pid, loc, f"""
WITH m AS (
  SELECT TIMESTAMP_TRUNC(period_start, MINUTE) AS minute, SUM(period_slot_ms) / 60000 AS slots,
    COUNT(DISTINCT job_id) AS active_jobs
  FROM {view}
  WHERE job_creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
    AND period_start >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY) AND {not_script}
  GROUP BY 1
)
SELECT TIMESTAMP_TRUNC(minute, HOUR) AS hour, SUM(slots) / 60 AS avg_slots, MAX(slots) AS peak_minute_slots,
  APPROX_QUANTILES(slots, 100)[OFFSET(95)] AS p95_active_minute_slots, MAX(active_jobs) AS peak_concurrent_jobs,
  AVG(active_jobs) AS avg_concurrent_jobs_active_minutes, COUNT(1) AS active_minutes
FROM m GROUP BY 1""", "timeline", "slot_timeline")


def _anonymize(ctx: Context, res: dict, pbi_patterns: list, rules: dict) -> None:
    """Calcula flags com o principal original e grava somente o pseudônimo."""
    red = ctx.redactor

    def flags(principal: str | None) -> dict:
        p = principal or ""
        return {"is_service_account": p.endswith("gserviceaccount.com"),
                "principal_domain": red.email_domain(p),
                "matches_bi": matches_any(p, pbi_patterns),
                "matches_powerbi": matches_any(p, ctx.config.get("powerbi", {}).get("principal_patterns", [])),
                "matches_etl": matches_any(p, rules.get("etl_principals", [])),
                "matches_ds": matches_any(p, rules.get("ds_principals", []))}

    for r in res.get("principals") or []:
        r.update(flags(r.get("principal")))
        r["principal"] = red.principal(r.get("principal"))
    for r in res.get("labels") or []:
        r.update({k: v for k, v in flags(r.get("principal")).items() if k.startswith("matches") or k == "is_service_account"})
        r["principal"] = red.principal(r.get("principal"))
        r["kv"] = red.text(r.get("kv"))
    for r in res.get("fingerprints") or []:
        f = flags(r.get("sample_principal"))
        r["sample_principal_is_sa"] = f["is_service_account"]
        r["sample_principal_matches_bi"] = f["matches_bi"]
        r["sample_principal_matches_etl"] = f["matches_etl"]
        r["sample_principal"] = red.principal(r.get("sample_principal"))
        r["label_kv"] = sorted({red.text(x) for x in r.get("label_kv") or []})
        r["refs"] = sorted(set(r.get("refs") or []))
        if "sample_query" in r:
            if ctx.config.get("sql", {}).get("store_sql_text"):
                r["sample_query"] = red.text(r["sample_query"])
            else:
                # modo full sem store_sql_text: analisa em memória e descarta o texto antes de gravar
                from analyzers.sql_complexity import analyze_text

                text = r.pop("sample_query") or ""
                a = analyze_text(text)
                r["sql_analysis"] = {k: a[k] for k in ("features", "complexity", "score", "anti_patterns",
                                                       "referenced_tables", "parser")}
    for r in res.get("bi_hourly") or []:
        r["matches_powerbi"] = matches_any(r.get("principal") or "", ctx.config.get("powerbi", {}).get("principal_patterns", []))
        r["principal"] = red.principal(r.get("principal"))
        r["refs"] = sorted(set(r.get("refs") or []))
    for key in ("tables_read", "writes"):
        for r in res.get(key) or []:
            raw = [x for k in ("principal_sample", "principals") if isinstance(r.get(k), list) for x in r[k]]
            r["bi_principals"] = sum(1 for p in raw if matches_any(p or "", pbi_patterns))
            r["powerbi_principals"] = sum(1 for p in raw if matches_any(p or "", ctx.config.get("powerbi", {}).get("principal_patterns", [])))
            r["etl_principals"] = sum(1 for p in raw if matches_any(p or "", rules.get("etl_principals", [])))
            for k in ("principal_sample", "principals"):
                if isinstance(r.get(k), list):
                    r[k] = [red.principal(p) for p in r[k]]
            if "label_kv" in r:
                r["label_kv"] = sorted({red.text(x) for x in r.get("label_kv") or []})


def label_value(label_kv: list[str], key: str) -> str | None:
    for kv in label_kv or []:
        if kv.startswith(key + "="):
            return kv.split("=", 1)[1]
    return None


FINGERPRINT_RE = re.compile(r"^[0-9a-f]{8,}$")
