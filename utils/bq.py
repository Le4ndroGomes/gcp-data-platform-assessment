"""Executor de consultas BigQuery de metadados com controle de custo.

Fluxo de cada consulta:
  1. dry run (jobs.insert com dryRun=true) -> bytes estimados + schema;
  2. bloqueia se a estimativa ultrapassar `max_bytes_billed` (por consulta) ou o
     orçamento total restante (`max_total_bytes_billed`);
  3. executa com `maximumBytesBilled` (o BigQuery rejeita a consulta se o limite
     for excedido, sem custo);
  4. pagina o resultado (getQueryResults).

Apenas instruções SELECT/WITH são aceitas.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from utils.http import ApiClient, ApiError
from utils.logging import get_logger

log = get_logger("bq")
BQ = "https://bigquery.googleapis.com/bigquery/v2"
_ALLOWED_SQL = re.compile(r"^\s*(--[^\n]*\n\s*)*(SELECT|WITH)\b", re.IGNORECASE)
_FORBIDDEN_SQL = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|CALL|EXECUTE\s+IMMEDIATE|EXPORT\s+DATA|LOAD\s+DATA)\b",
                            re.IGNORECASE)


class CostLimitExceeded(Exception):
    pass


class UnsafeQuery(Exception):
    pass


@dataclass
class QueryStats:
    queries: int = 0
    dry_runs: int = 0
    skipped_cost: int = 0
    bytes_estimated: int = 0
    bytes_billed: int = 0
    failures: int = 0
    in_flight_reserved: int = 0
    log: list = field(default_factory=list)


def region_qualifier(location: str) -> str:
    return f"region-{location.lower()}"


def info_schema(project: str, location: str, view: str) -> str:
    return f"`{project}`.`{region_qualifier(location)}`.INFORMATION_SCHEMA.{view}"


def dataset_info_schema(project: str, dataset: str, view: str) -> str:
    return f"`{project}`.`{dataset}`.INFORMATION_SCHEMA.{view}"


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"", "''", sql)


def assert_read_only(sql: str) -> None:
    if not _ALLOWED_SQL.match(sql):
        raise UnsafeQuery("Somente SELECT/WITH são permitidos")
    if _FORBIDDEN_SQL.search(_strip_comments(sql)):
        raise UnsafeQuery("Instrução não somente-leitura detectada")


# ----------------------------------------------------------- row decoding
def _convert(value: Any, f: dict) -> Any:
    if value is None:
        return None
    mode = f.get("mode", "NULLABLE")
    if mode == "REPEATED":
        return [_convert(v.get("v") if isinstance(v, dict) else v, {**f, "mode": "NULLABLE"}) for v in value]
    t = (f.get("type") or "STRING").upper()
    if t in ("RECORD", "STRUCT"):
        return decode_row(value, f.get("fields", []))
    if t in ("INTEGER", "INT64"):
        return int(value)
    if t in ("FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"):
        return float(value)
    if t in ("BOOLEAN", "BOOL"):
        return value in (True, "true", "TRUE", "1")
    if t == "TIMESTAMP":
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except (TypeError, ValueError):
            return value
    return value


def decode_row(row: dict, fields: list[dict]) -> dict:
    cells = row.get("f", [])
    return {f["name"]: _convert(c.get("v"), f) for f, c in zip(fields, cells)}


# ------------------------------------------------------------------ runner
class BigQueryRunner:
    def __init__(self, api: ApiClient, *, max_bytes_billed: int, max_total_bytes_billed: int,
                 execution_project: str | None = None, job_timeout_s: int = 300, labels: dict | None = None,
                 dry_run_first: bool = True):
        self.api = api
        self.max_bytes = int(max_bytes_billed)
        self.max_total = int(max_total_bytes_billed)
        self.execution_project = execution_project
        self.job_timeout_s = job_timeout_s
        self.labels = labels or {"purpose": "platform-assessment"}
        self.dry_run_first = dry_run_first
        self.stats = QueryStats()
        self._lock = threading.Lock()
        self._schema_cache: dict[tuple, dict | None] = {}
        self.schema_errors: dict[tuple, str] = {}

    def _job_project(self, project: str) -> str:
        return self.execution_project or project

    # --------------------------------------------------------------- dry run
    def dry_run(self, project: str, location: str, sql: str) -> tuple[int, list[dict]]:
        assert_read_only(sql)
        body = {
            "configuration": {"dryRun": True, "query": {"query": sql, "useLegacySql": False}},
            "jobReference": {"projectId": self._job_project(project), "location": location},
        }
        job = self.api.post(f"{BQ}/projects/{self._job_project(project)}/jobs", json_body=body)
        stats = job.get("statistics", {}) or {}
        q = stats.get("query", {}) or {}
        est = int(q.get("totalBytesProcessed") or stats.get("totalBytesProcessed") or 0)
        schema = (q.get("schema") or {}).get("fields", [])
        with self._lock:
            self.stats.dry_runs += 1
        return est, schema

    def view_schema(self, project: str, location: str, view_ref: str) -> dict | None:
        """Retorna {coluna: campo} disponível na visão (via dry run de SELECT *), ou None se inacessível."""
        key = (project, location, view_ref)
        if key in self._schema_cache:
            return self._schema_cache[key]
        try:
            _, fields = self.dry_run(project, location, f"SELECT * FROM {view_ref}")
            result = {f["name"].lower(): f for f in fields}
        except ApiError as exc:
            log.debug("Visão indisponível %s: %s", view_ref, exc)
            self.schema_errors[key] = (exc.message or "")[:400]
            result = None
        self._schema_cache[key] = result
        return result

    # ----------------------------------------------------------------- query
    def query(self, project: str, location: str, sql: str, *, name: str = "query",
              max_rows: int = 200_000, max_bytes: int | None = None) -> list[dict]:
        assert_read_only(sql)
        cap = int(max_bytes or self.max_bytes)
        est = None
        if self.dry_run_first:
            est, _ = self.dry_run(project, location, sql)
            # Consultas de INFORMATION_SCHEMA têm cobrança mínima de 10 MB por consulta (on-demand).
            billable_est = max(est, 10 * 1024 * 1024)
            with self._lock:
                remaining = self.max_total - self.stats.bytes_billed - self.stats.in_flight_reserved
                if billable_est > cap or billable_est > remaining:
                    self.stats.skipped_cost += 1
                    self.stats.log.append({"name": name, "project": project, "location": location,
                                           "estimated_bytes": est, "status": "skipped_cost_limit"})
                    raise CostLimitExceeded(
                        f"{name}: estimativa {est:,} bytes excede o limite (por consulta {cap:,}; "
                        f"restante {max(remaining, 0):,})")
                self.stats.bytes_estimated += est
                self.stats.in_flight_reserved += billable_est

        body = {
            "query": sql,
            "useLegacySql": False,
            "location": location,
            "maximumBytesBilled": str(cap),
            "timeoutMs": 30_000,
            "maxResults": 10_000,
            "useQueryCache": True,
            "labels": self.labels,
        }
        started = time.time()
        reserved = max(est, 10 * 1024 * 1024) if est is not None else 0
        try:
            resp = self.api.post(f"{BQ}/projects/{self._job_project(project)}/queries", json_body=body)
            fields = (resp.get("schema") or {}).get("fields", [])
            job_ref = resp.get("jobReference", {}) or {}
            while not resp.get("jobComplete", False):
                if time.time() - started > self.job_timeout_s:
                    raise ApiError(0, f"timeout de {self.job_timeout_s}s", name, "timeout")
                resp = self._get_results(job_ref, location, None, timeout_ms=30_000)
                fields = (resp.get("schema") or {}).get("fields", fields)
            rows = [decode_row(r, fields) for r in resp.get("rows", []) or []]
            token = resp.get("pageToken")
            while token and len(rows) < max_rows:
                page = self._get_results(job_ref, location, token)
                rows.extend(decode_row(r, fields) for r in page.get("rows", []) or [])
                token = page.get("pageToken")
            billed = int(resp.get("totalBytesBilled") or resp.get("totalBytesProcessed") or 0)
            with self._lock:
                self.stats.queries += 1
                self.stats.bytes_billed += billed
                self.stats.log.append({"name": name, "project": project, "location": location,
                                       "estimated_bytes": est, "billed_bytes": billed, "rows": len(rows),
                                       "seconds": round(time.time() - started, 2), "status": "ok",
                                       "truncated": bool(token)})
            return rows[:max_rows]
        except ApiError as exc:
            with self._lock:
                self.stats.failures += 1
                self.stats.log.append({"name": name, "project": project, "location": location,
                                       "status": f"error:{exc.kind}", "message": exc.message[:300]})
            raise
        finally:
            with self._lock:
                self.stats.in_flight_reserved -= reserved

    def _get_results(self, job_ref: dict, location: str, page_token: str | None, timeout_ms: int = 10_000) -> dict:
        params = {"location": job_ref.get("location") or location, "timeoutMs": timeout_ms, "maxResults": 10_000}
        if page_token:
            params["pageToken"] = page_token
        return self.api.get(f"{BQ}/projects/{job_ref['projectId']}/queries/{job_ref['jobId']}", params=params)


class ColumnSelector:
    """Monta listas de colunas tolerantes a variações de schema entre versões/regiões.

    Exemplo: sel.col("query_info.query_hashes.normalized_literals", "STRING", alias="qh")
    gera a expressão real se o campo existir, ou CAST(NULL AS STRING) caso contrário.
    """

    def __init__(self, schema: dict | None):
        self.schema = schema or {}
        self.missing: list[str] = []

    def has(self, path: str) -> bool:
        parts = path.lower().split(".")
        f = self.schema.get(parts[0])
        for p in parts[1:]:
            if not f:
                return False
            f = {x["name"].lower(): x for x in f.get("fields", [])}.get(p)
        return f is not None

    def expr(self, path: str, sql_type: str = "STRING", expr: str | None = None) -> str:
        if self.has(path):
            return expr or path
        self.missing.append(path)
        return f"CAST(NULL AS {sql_type})"

    def col(self, path: str, sql_type: str = "STRING", alias: str | None = None, expr: str | None = None) -> str:
        alias = alias or path.split(".")[-1]
        return f"{self.expr(path, sql_type, expr)} AS {alias}"
