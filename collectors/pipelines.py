"""Ingestão, pipelines e orquestração (item 7).

Cada tecnologia gera registros normalizados (`pipeline_inventory`) com:
id, nome, tecnologia, origem(s), destino(s), agendamento, frequência estimada,
estado, execuções observadas, duração média/máxima, taxa de falha, estratégia de
carga (full/incremental/CDC/streaming), retry, confiança e referência de código.

Princípios: somente leitura; segredos, variáveis de ambiente, corpos de
requisições e textos SQL não são persistidos (texto SQL apenas com
--include-sql + sql.store_sql_text). Origens fora do GCP viram
"dependências externas a investigar".
"""
from __future__ import annotations

import time

import re
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from analyzers.sql_complexity import analyze_text
from core.context import Context
from utils.http import ApiError, NotFound
from utils.logging import get_logger
from utils.schedule import frequency_label, runs_per_day

log = get_logger("pipelines")
MODULE = "pipelines"
DTS = "https://bigquerydatatransfer.googleapis.com/v1"
COMPOSER = "https://composer.googleapis.com/v1"
DATAFLOW = "https://dataflow.googleapis.com/v1b3"
DATAPROC = "https://dataproc.googleapis.com/v1"
DATAFORM = "https://dataform.googleapis.com/v1beta1"
DATAFUSION = "https://datafusion.googleapis.com/v1"
PUBSUB = "https://pubsub.googleapis.com/v1"
DATASTREAM = "https://datastream.googleapis.com/v1"
SCHEDULER = "https://cloudscheduler.googleapis.com/v1"
WORKFLOWS = "https://workflows.googleapis.com/v1"
FUNCTIONS = "https://cloudfunctions.googleapis.com/v2"
RUN = "https://run.googleapis.com/v2"
BQCONN = "https://bigqueryconnection.googleapis.com/v1"

TERMINAL_FAIL = {"FAILED", "JOB_STATE_FAILED", "failed", "ERROR", "CANCELLED", "JOB_STATE_CANCELLED"}
TERMINAL_OK = {"SUCCEEDED", "JOB_STATE_DONE", "success", "DONE"}
SAFE_DTS_PARAMS = {"destination_table_name_template", "write_disposition", "partitioning_field", "data_path_template",
                   "file_format", "skip_leading_rows", "field_delimiter", "max_bad_records", "source_dataset_id",
                   "source_project_id", "overwrite_destination_table", "table_name_patterns", "assets",
                   "connector.database", "connector.schema"}


# ------------------------------------------------------------------ helpers
def _parse_ts(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_s(start, end) -> float | None:
    a, b = _parse_ts(start), _parse_ts(end)
    if a and b and b >= a:
        return (b - a).total_seconds()
    return None


def _run_stats(runs: list[dict]) -> dict:
    """runs: [{state, duration_s, start}] -> estatísticas agregadas."""
    durs = [r["duration_s"] for r in runs if r.get("duration_s") is not None]
    finished = [r for r in runs if r.get("state") in TERMINAL_FAIL | TERMINAL_OK]
    failed = [r for r in finished if r.get("state") in TERMINAL_FAIL]
    return {
        "runs_observed": len(runs),
        "avg_duration_s": round(statistics.mean(durs), 1) if durs else None,
        "max_duration_s": round(max(durs), 1) if durs else None,
        "p95_duration_s": round(sorted(durs)[int(0.95 * (len(durs) - 1))], 1) if durs else None,
        "failure_rate": round(len(failed) / len(finished), 3) if finished else None,
        "last_run": max((r.get("start") or "" for r in runs), default=None) or None,
    }


def _record(**kw) -> dict:
    base = {"id": None, "name": None, "technology": None, "project_id": None, "location": None, "sources": [],
            "targets": [], "schedule": None, "runs_per_day": None, "frequency": None, "state": None,
            "runs_observed": None, "avg_duration_s": None, "max_duration_s": None, "p95_duration_s": None,
            "failure_rate": None, "last_run": None, "load_strategy": "desconhecida", "processing": "batch",
            "retry": None, "sla": None, "dependencies": [], "code_ref": None, "service_account": None,
            "confidence": "medium", "notes": []}
    base.update(kw)
    if base["runs_per_day"] is None and base["schedule"] is not None:
        base["runs_per_day"] = runs_per_day(base["schedule"])
    base["frequency"] = base["frequency"] or frequency_label(base["runs_per_day"])
    return base


def _enabled(ctx: Context, pid: str, api: str) -> bool | None:
    svcs = (ctx.load_raw("discovery", "services_enabled") or {}).get(pid)
    if svcs is None or not svcs:
        return None  # desconhecido -> tenta
    return api in svcs


def _disabled(ctx: Context, pid: str, api: str, component: str) -> bool:
    """API desabilitada no projeto => ausência de recursos confirmada (não é falta de visibilidade)."""
    if _enabled(ctx, pid, api) is False:
        ctx.coverage.ok(MODULE, component, 0)
        return True
    return False


def _locations(ctx: Context, pid: str, service_key: str) -> list[str]:
    """Localizações a consultar: assets do serviço (CAI) + regiões configuradas + regiões BigQuery regionais."""
    assets = (ctx.load_raw("discovery", "assets") or {}).get(pid)
    locs: set[str] = set()
    if assets is not None:
        locs |= {a["location"] for a in assets if a.get("service") == service_key and a.get("location")
                 and a["location"] != "global"}
        if assets:  # CAI respondeu: se não há assets desse serviço, não há o que listar
            return sorted(locs)
    locs |= {r for r in ctx.config["gcp"].get("regions") or []}
    for loc in ctx.regions_for(pid):
        if loc.lower() in ("us", "eu"):
            locs.add("us-central1" if loc.lower() == "us" else "europe-west1")
        else:
            locs.add(loc.lower())
    return sorted(locs)


def _try(ctx: Context, component: str, scope: str, fn, default=None):
    try:
        out = fn()
        ctx.coverage.ok(MODULE, component, len(out) if isinstance(out, list) else 1)
        return out
    except NotFound:
        ctx.coverage.ok(MODULE, component, 0)
        return default if default is not None else []
    except ApiError as exc:
        ctx.coverage.fail(MODULE, component, exc, scope=scope)
        return default if default is not None else []


def _strip_name(name: str) -> str:
    """Agrupa execuções do mesmo pipeline removendo sufixos de data/hora/ids."""
    return re.sub(r"([-_]?\d{6,}|[-_][0-9a-f]{8,}|[-_]\d{4}-\d{2}-\d{2}.*)$", "", name or "")


# --------------------------------------------------------------------- DTS
def _transfers(ctx: Context, pid: str) -> tuple[list, list]:
    pipes, raw = [], []
    max_runs = int(ctx.config["pipelines"].get("max_transfer_runs", 50))
    store_sql = ctx.include_sql and ctx.config.get("sql", {}).get("store_sql_text")
    for loc in sorted({loc.lower() for loc in ctx.regions_for(pid)}):
        configs = _try(ctx, "bigquery_transfers", f"{pid}/{loc}", lambda: list(
            ctx.gcp.paginate(f"{DTS}/projects/{pid}/locations/{loc}/transferConfigs", "transferConfigs",
                             {"pageSize": 1000})))
        for c in configs:
            params = c.get("params") or {}
            ds_id = c.get("dataSourceId")
            sql_info = None
            if ds_id == "scheduled_query" and params.get("query"):
                a = analyze_text(params["query"])
                sql_info = {k: a[k] for k in ("complexity", "score", "anti_patterns", "referenced_tables", "features")}
            runs_raw = _try(ctx, "bigquery_transfer_runs", c.get("name", ""), lambda: list(
                ctx.gcp.paginate(f"{DTS}/{c['name']}/runs", "transferRuns", {"pageSize": max_runs}, max_items=max_runs)))
            runs = [{"state": r.get("state"), "duration_s": _duration_s(r.get("startTime"), r.get("endTime")),
                     "start": r.get("startTime") or r.get("runTime")} for r in runs_raw]
            dest_ds = c.get("destinationDatasetId")
            target = f"{pid}.{dest_ds}" if dest_ds else None
            tmpl = params.get("destination_table_name_template")
            if target and tmpl:
                target = f"{target}.{tmpl}"
            sources = sql_info["referenced_tables"] if sql_info else [f"external:{ds_id}"]
            write_disp = (params.get("write_disposition") or "").upper()
            strategy = ("full load" if write_disp == "WRITE_TRUNCATE" else "incremental" if write_disp == "WRITE_APPEND"
                        else "incremental (MERGE)" if sql_info and sql_info["features"].get("merge") else "desconhecida")
            rec = _record(
                id=c.get("name"), name=c.get("displayName"), technology=f"BigQuery DTS ({ds_id})", project_id=pid,
                location=loc, sources=sources, targets=[target] if target else [], schedule=c.get("schedule"),
                state="DISABLED" if c.get("disabled") else c.get("state"), load_strategy=strategy,
                service_account=ctx.redactor.principal((c.get("ownerInfo") or {}).get("email")),
                confidence="high", **_run_stats(runs))
            if ds_id != "scheduled_query":
                rec["notes"].append(f"Fonte gerenciada pelo DTS: {ds_id} (dependência externa a investigar)")
            if sql_info:
                rec["sql_complexity"] = sql_info["complexity"]
                rec["sql_anti_patterns"] = sql_info["anti_patterns"]
            pipes.append(rec)
            safe = {k: v for k, v in params.items() if k in SAFE_DTS_PARAMS}
            if store_sql and params.get("query"):
                safe["query"] = ctx.redactor.text(params["query"])
            raw.append({"name": c.get("name"), "display_name": c.get("displayName"), "data_source_id": ds_id,
                        "destination_dataset": dest_ds, "schedule": c.get("schedule"), "disabled": c.get("disabled"),
                        "state": c.get("state"), "params": ctx.redactor.scrub_secrets(safe),
                        "param_keys": sorted(params), "sql_analysis": sql_info, "update_time": c.get("updateTime")})
    return pipes, raw


# ---------------------------------------------------------------- Composer
def _composer(ctx: Context, pid: str) -> tuple[list, list]:
    if _disabled(ctx, pid, "composer.googleapis.com", "composer"):
        return [], []
    pipes, raw = [], []
    for loc in _locations(ctx, pid, "composer"):
        envs = _try(ctx, "composer", f"{pid}/{loc}", lambda: list(
            ctx.gcp.paginate(f"{COMPOSER}/projects/{pid}/locations/{loc}/environments", "environments", {"pageSize": 100})))
        for e in envs:
            cfg = e.get("config") or {}
            env_rec = {"name": e.get("name"), "state": e.get("state"),
                       "image_version": (cfg.get("softwareConfig") or {}).get("imageVersion"),
                       "environment_size": cfg.get("environmentSize"), "node_count": cfg.get("nodeCount"),
                       "private": (cfg.get("privateEnvironmentConfig") or {}).get("enablePrivateEnvironment"),
                       "service_account": ctx.redactor.principal((cfg.get("nodeConfig") or {}).get("serviceAccount")),
                       "dag_bucket": (cfg.get("dagGcsPrefix") or "").replace("gs://", "").split("/")[0] or None,
                       "workloads": cfg.get("workloadsConfig"), "dags": []}
            uri = cfg.get("airflowUri")
            if uri and ctx.config["pipelines"].get("airflow_rest_api", True):
                env_rec["dags"] = _airflow_dags(ctx, uri, e.get("name"))
            for d in env_rec["dags"]:
                pipes.append(_record(
                    id=f"{e.get('name')}/dags/{d['dag_id']}", name=d["dag_id"], technology="Cloud Composer (Airflow)",
                    project_id=pid, location=loc, schedule=d.get("schedule"), runs_per_day=d.get("runs_per_day"),
                    state="PAUSED" if d.get("is_paused") else "ACTIVE", retry=d.get("retries"), sla=d.get("sla"),
                    dependencies=d.get("operators", []), code_ref=d.get("fileloc_hash"),
                    sources=d.get("inferred_sources", []), targets=d.get("inferred_targets", []),
                    confidence="high" if d.get("runs") else "medium",
                    **_run_stats(d.get("runs", []))))
                d.pop("runs", None)
            raw.append(env_rec)
    return pipes, raw


def _airflow_dags(ctx: Context, airflow_uri: str, env_name: str) -> list[dict]:
    base = airflow_uri.rstrip("/") + "/api/v1"
    max_runs = int(ctx.config["pipelines"].get("max_dag_runs_per_dag", 50))
    dags: list[dict] = []
    try:
        offset = 0
        while True:
            page = ctx.gcp.get(f"{base}/dags", params={"limit": 100, "offset": offset, "only_active": "true"})
            items = page.get("dags") or []
            dags.extend(items)
            offset += len(items)
            if not items or offset >= int(page.get("total_entries") or 0) or offset > 5000:
                break
        ctx.coverage.ok(MODULE, "airflow_dags", len(dags))
    except ApiError as exc:
        ctx.coverage.fail(MODULE, "airflow_dags", exc, scope=env_name,
                          permission_hint="roles/composer.user + papel Airflow 'Viewer' (REST API do Airflow)")
        return []
    out = []
    since = datetime.now(timezone.utc) - timedelta(days=ctx.days)
    for d in dags:
        dag_id = d.get("dag_id")
        sched = d.get("schedule_interval") if d.get("schedule_interval") is not None else d.get("timetable_description")
        rec = {"dag_id": dag_id, "is_paused": d.get("is_paused"), "schedule": sched if isinstance(sched, str) else None,
               "runs_per_day": runs_per_day(sched), "tags": [t.get("name") for t in d.get("tags") or []],
               "owners": len(d.get("owners") or []), "fileloc_hash": ctx.redactor.pseudonym(d.get("fileloc"), "dagfile"),
               "operators": [], "retries": None, "runs": []}
        try:
            runs = ctx.gcp.get(f"{base}/dags/{dag_id}/dagRuns", params={"limit": max_runs, "order_by": "-execution_date"})
            rec["runs"] = [{"state": r.get("state"), "start": r.get("start_date"),
                            "duration_s": _duration_s(r.get("start_date"), r.get("end_date"))}
                           for r in runs.get("dag_runs") or [] if (_parse_ts(r.get("start_date")) or since) >= since]
            tasks = ctx.gcp.get(f"{base}/dags/{dag_id}/tasks")
            ops = defaultdict(int)
            retries = []
            for t in tasks.get("tasks") or []:
                ops[(t.get("class_ref") or {}).get("class_name") or t.get("operator_name") or "?"] += 1
                if t.get("retries") is not None:
                    retries.append(t.get("retries"))
            rec["operators"] = [f"{k} x{v}" for k, v in sorted(ops.items(), key=lambda x: -x[1])]
            rec["task_count"] = sum(ops.values())
            rec["retries"] = max(retries) if retries else None
            ctx.coverage.ok(MODULE, "airflow_dag_runs", 1)
        except ApiError as exc:
            ctx.coverage.fail(MODULE, "airflow_dag_runs", exc, scope=f"{env_name}/{dag_id}")
        out.append(rec)
    return out


# ---------------------------------------------------------------- Dataflow
def _dataflow(ctx: Context, pid: str) -> tuple[list, list]:
    if _disabled(ctx, pid, "dataflow.googleapis.com", "dataflow"):
        return [], []
    cap = int(ctx.config["pipelines"].get("max_dataflow_jobs", 500))
    since = datetime.now(timezone.utc) - timedelta(days=ctx.days)
    jobs = _try(ctx, "dataflow", pid, lambda: list(ctx.gcp.paginate(
        f"{DATAFLOW}/projects/{pid}/jobs:aggregated", "jobs", {"filter": "ALL", "pageSize": 200}, max_items=cap)))
    jobs = [j for j in jobs if (_parse_ts(j.get("createTime")) or since) >= since]
    groups: dict[str, list] = defaultdict(list)
    for j in jobs:
        groups[_strip_name(j.get("name"))].append(j)
    pipes, raw = [], []
    for name, js in groups.items():
        latest = max(js, key=lambda x: x.get("createTime") or "")
        detail = {}
        try:
            detail = ctx.gcp.get(f"{DATAFLOW}/projects/{pid}/locations/{latest.get('location')}/jobs/{latest['id']}",
                                 params={"view": "JOB_VIEW_ALL",
                                         "fields": "id,type,jobMetadata,environment/serviceAccountEmail,environment/workerRegion"})
            ctx.coverage.ok(MODULE, "dataflow_details", 1)
        except ApiError as exc:
            ctx.coverage.fail(MODULE, "dataflow_details", exc, scope=latest.get("id", ""))
        md = detail.get("jobMetadata") or {}
        sources, targets = [], []
        for b in md.get("bigqueryDetails") or []:
            ref = ".".join(x for x in [b.get("projectId"), b.get("dataset"), b.get("table")] if x)
            sources.append(f"bigquery:{ref}" if ref else "bigquery:(query)")
        for p in md.get("pubsubDetails") or []:
            sources.append(f"pubsub:{p.get('topic') or p.get('subscription')}")
        for f in md.get("fileDetails") or []:
            sources.append(f"gcs:{(f.get('filePattern') or '').replace('gs://', '').split('/')[0]}")
        for s in md.get("spannerDetails") or []:
            sources.append(f"spanner:{s.get('instanceId')}/{s.get('databaseId')}")
        for s in md.get("bigTableDetails") or []:
            sources.append(f"bigtable:{s.get('instanceId')}/{s.get('tableId')}")
        streaming = latest.get("type") == "JOB_TYPE_STREAMING"
        runs = [{"state": j.get("currentState"), "start": j.get("startTime") or j.get("createTime"),
                 "duration_s": None if j.get("currentState") == "JOB_STATE_RUNNING" else
                 _duration_s(j.get("startTime") or j.get("createTime"), j.get("currentStateTime"))} for j in js]
        rec = _record(id=f"dataflow:{pid}:{name}", name=name, technology="Dataflow", project_id=pid,
                      location=latest.get("location"), sources=sorted(set(sources)), targets=[],
                      state=latest.get("currentState"), processing="streaming" if streaming else "batch",
                      load_strategy="streaming" if streaming else "desconhecida",
                      runs_per_day=None if streaming else round(len(js) / max(ctx.days, 1), 3),
                      service_account=ctx.redactor.principal((detail.get("environment") or {}).get("serviceAccountEmail")),
                      confidence="high" if md else "medium", **_run_stats(runs))
        rec["notes"].append("jobMetadata lista E/S detectadas pelo SDK; destinos (sinks) nem sempre aparecem")
        pipes.append(rec)
        raw.append({"pipeline": name, "jobs": len(js), "type": latest.get("type"), "sdk": (md.get("sdkVersion") or {}).get("version"),
                    "location": latest.get("location"), "io": sorted(set(sources))})
    return pipes, raw


# ---------------------------------------------------------------- Dataproc
def _dataproc(ctx: Context, pid: str) -> tuple[list, list]:
    if _disabled(ctx, pid, "dataproc.googleapis.com", "dataproc"):
        return [], []
    pipes, raw = [], []
    since = datetime.now(timezone.utc) - timedelta(days=ctx.days)
    for region in _locations(ctx, pid, "dataproc"):
        clusters = _try(ctx, "dataproc", f"{pid}/{region}", lambda: list(ctx.gcp.paginate(
            f"{DATAPROC}/projects/{pid}/regions/{region}/clusters", "clusters", {"pageSize": 100})))
        for c in clusters:
            cfg = c.get("config") or {}
            raw.append({"type": "cluster", "name": c.get("clusterName"), "region": region,
                        "state": (c.get("status") or {}).get("state"),
                        "workers": (cfg.get("workerConfig") or {}).get("numInstances"),
                        "worker_machine": ((cfg.get("workerConfig") or {}).get("machineTypeUri") or "").split("/")[-1],
                        "secondary_workers": (cfg.get("secondaryWorkerConfig") or {}).get("numInstances"),
                        "image_version": (cfg.get("softwareConfig") or {}).get("imageVersion"),
                        "autoscaling": bool(cfg.get("autoscalingConfig")),
                        "optional_components": (cfg.get("softwareConfig") or {}).get("optionalComponents")})
        jobs = _try(ctx, "dataproc_jobs", f"{pid}/{region}", lambda: list(ctx.gcp.paginate(
            f"{DATAPROC}/projects/{pid}/regions/{region}/jobs", "jobs", {"pageSize": 100}, max_items=1000)))
        batches = _try(ctx, "dataproc_batches", f"{pid}/{region}", lambda: list(ctx.gcp.paginate(
            f"{DATAPROC}/projects/{pid}/locations/{region}/batches", "batches", {"pageSize": 100}, max_items=1000)))
        groups: dict[str, list] = defaultdict(list)
        for j in jobs:
            kind = next((k for k in j if k.endswith("Job") and isinstance(j[k], dict)), "job")
            first = (j.get("statusHistory") or [{}])[0].get("stateStartTime") or (j.get("status") or {}).get("stateStartTime")
            if (_parse_ts(first) or since) < since:
                continue
            key = (j.get("labels") or {}).get("goog-dataproc-workflow-template-id") or _strip_name((j.get("reference") or {}).get("jobId"))
            groups[f"{kind}:{key}"].append({"state": "DONE" if (j.get("status") or {}).get("state") == "DONE" else
                                            (j.get("status") or {}).get("state"),
                                            "start": first, "duration_s": _duration_s(first, (j.get("status") or {}).get("stateStartTime")),
                                            "cluster": (j.get("placement") or {}).get("clusterName")})
        for b in batches:
            if (_parse_ts(b.get("createTime")) or since) < since:
                continue
            kind = next((k for k in b if k.endswith("Batch") and isinstance(b[k], dict)), "batch")
            groups[f"serverless-{kind}:{_strip_name(b.get('name', '').split('/')[-1])}"].append(
                {"state": "SUCCEEDED" if b.get("state") == "SUCCEEDED" else b.get("state"), "start": b.get("createTime"),
                 "duration_s": _duration_s(b.get("createTime"), b.get("stateTime"))})
        for key, runs in groups.items():
            pipes.append(_record(id=f"dataproc:{pid}:{region}:{key}", name=key.split(":", 1)[1], technology=f"Dataproc ({key.split(':')[0]})",
                                 project_id=pid, location=region, runs_per_day=round(len(runs) / max(ctx.days, 1), 3),
                                 confidence="medium", **_run_stats(runs)))
    return pipes, raw


# ---------------------------------------------------------------- Dataform
def _dataform(ctx: Context, pid: str) -> tuple[list, list, list]:
    if _disabled(ctx, pid, "dataform.googleapis.com", "dataform"):
        return [], [], []
    pipes, raw, edges = [], [], []
    for loc in _locations(ctx, pid, "dataform"):
        repos = _try(ctx, "dataform", f"{pid}/{loc}", lambda: list(ctx.gcp.paginate(
            f"{DATAFORM}/projects/{pid}/locations/{loc}/repositories", "repositories", {"pageSize": 100})))
        for r in repos:
            name = r.get("name")
            wcfgs = _try(ctx, "dataform_workflows", name, lambda: list(ctx.gcp.paginate(
                f"{DATAFORM}/{name}/workflowConfigs", "workflowConfigs", {"pageSize": 100})))
            invs = _try(ctx, "dataform_workflows", name, lambda: list(ctx.gcp.paginate(
                f"{DATAFORM}/{name}/workflowInvocations", "workflowInvocations", {"pageSize": 100}, max_items=200)))
            runs = [{"state": i.get("state"), "start": (i.get("invocationTiming") or {}).get("startTime"),
                     "duration_s": _duration_s((i.get("invocationTiming") or {}).get("startTime"),
                                               (i.get("invocationTiming") or {}).get("endTime"))} for i in invs]
            actions = []
            if ctx.config["pipelines"].get("dataform_compilation_graph", True) and invs:
                comp = next((i.get("compilationResult") for i in invs if i.get("compilationResult")), None)
                if comp:
                    actions = _try(ctx, "dataform_graph", comp, lambda: list(ctx.gcp.paginate(
                        f"{DATAFORM}/{comp}:query", "compilationResultActions",
                        {"pageSize": 1000, "fields": "compilationResultActions(target,relation/dependencyTargets,"
                         "relation/relationType,assertion/dependencyTargets,declaration,operations/dependencyTargets),nextPageToken"})))
            targets = []
            for a in actions:
                t = a.get("target") or {}
                tref = ".".join(x for x in [t.get("database"), t.get("schema"), t.get("name")] if x)
                kind = "declaration" if a.get("declaration") else ((a.get("relation") or {}).get("relationType") or
                                                                    ("assertion" if a.get("assertion") else "operation"))
                deps = ((a.get("relation") or {}).get("dependencyTargets") or (a.get("assertion") or {}).get("dependencyTargets")
                        or (a.get("operations") or {}).get("dependencyTargets") or [])
                if kind not in ("declaration", "assertion"):
                    targets.append(tref)
                for d in deps:
                    dref = ".".join(x for x in [d.get("database"), d.get("schema"), d.get("name")] if x)
                    edges.append({"source": dref, "target": tref, "via": "dataform", "repository": name, "kind": kind})
            schedules = [w.get("cronSchedule") for w in wcfgs if w.get("cronSchedule")]
            rpd = sum(runs_per_day(s) or 0 for s in schedules) or None
            pipes.append(_record(
                id=name, name=name.split("/")[-1], technology="Dataform", project_id=pid, location=loc,
                schedule=schedules[0] if len(schedules) == 1 else (f"{len(schedules)} workflow configs" if schedules else None),
                runs_per_day=rpd, targets=sorted(set(targets))[:500], sources=sorted({e["source"] for e in edges if e["repository"] == name} - set(targets))[:500],
                state="ACTIVE" if wcfgs else "SEM AGENDAMENTO", load_strategy="incremental" if any(
                    (a.get("relation") or {}).get("relationType") == "INCREMENTAL_TABLE" for a in actions) else "full load" if actions else "desconhecida",
                code_ref=ctx.redactor.host(((r.get("gitRemoteSettings") or {}).get("url"))),
                service_account=ctx.redactor.principal(r.get("serviceAccount")), confidence="high" if actions else "medium",
                **_run_stats(runs)))
            raw.append({"repository": name, "workflow_configs": len(wcfgs), "invocations_observed": len(invs),
                        "actions": len(actions), "relation_types": dict(_count(a.get("relation", {}).get("relationType")
                                                                             for a in actions if a.get("relation")))})
    return pipes, raw, edges


def _count(it):
    out = defaultdict(int)
    for x in it:
        out[x or "?"] += 1
    return out


# ------------------------------------------------------- Data Fusion / PubSub
def _datafusion(ctx: Context, pid: str) -> tuple[list, list]:
    if _disabled(ctx, pid, "datafusion.googleapis.com", "datafusion"):
        return [], []
    insts = _try(ctx, "datafusion", pid, lambda: list(ctx.gcp.paginate(
        f"{DATAFUSION}/projects/{pid}/locations/-/instances", "instances", {"pageSize": 100})))
    pipes, raw = [], []
    for i in insts:
        apps = []
        ep = i.get("apiEndpoint")
        if ep and i.get("state") == "RUNNING":
            try:
                for ns in ctx.gcp.get(f"{ep}/v3/namespaces") or []:
                    nsn = ns.get("name") if isinstance(ns, dict) else ns
                    for app in ctx.gcp.get(f"{ep}/v3/namespaces/{nsn}/apps",
                                           params={"artifactName": "cdap-data-pipeline,cdap-data-streams"}) or []:
                        apps.append({"namespace": nsn, "name": app.get("name"),
                                     "artifact": (app.get("artifact") or {}).get("name")})
                ctx.coverage.ok(MODULE, "datafusion_pipelines", len(apps))
            except ApiError as exc:
                ctx.coverage.fail(MODULE, "datafusion_pipelines", exc, scope=i.get("name", ""),
                                  permission_hint="roles/datafusion.viewer + acesso à instância CDAP")
        for a in apps:
            pipes.append(_record(id=f"{i.get('name')}/{a['namespace']}/{a['name']}", name=a["name"],
                                 technology="Cloud Data Fusion", project_id=pid,
                                 location=i.get("name", "").split("/")[3] if i.get("name") else None,
                                 processing="streaming" if a["artifact"] == "cdap-data-streams" else "batch",
                                 confidence="low", notes=["Agendamentos/fontes do CDAP não coletados (detalhe por pipeline)"]))
        raw.append({"name": i.get("name"), "type": i.get("type"), "state": i.get("state"), "version": i.get("version"),
                    "pipelines": len(apps)})
    return pipes, raw


def _pubsub(ctx: Context, pid: str) -> tuple[list, dict]:
    if _disabled(ctx, pid, "pubsub.googleapis.com", "pubsub"):
        return [], {}
    topics = _try(ctx, "pubsub", pid, lambda: list(ctx.gcp.paginate(f"{PUBSUB}/projects/{pid}/topics", "topics", {"pageSize": 1000})))
    subs = _try(ctx, "pubsub", pid, lambda: list(ctx.gcp.paginate(f"{PUBSUB}/projects/{pid}/subscriptions", "subscriptions", {"pageSize": 1000})))
    pipes = []
    out_subs = []
    for s in subs:
        bq = (s.get("bigqueryConfig") or {}).get("table")
        gcs = (s.get("cloudStorageConfig") or {}).get("bucket")
        push = (s.get("pushConfig") or {}).get("pushEndpoint")
        kind = "bigquery" if bq else "cloud_storage" if gcs else "push" if push else "pull"
        out_subs.append({"name": s.get("name", "").split("/")[-1], "topic": s.get("topic", "").split("/")[-1],
                         "delivery": kind, "bigquery_table": bq.replace(":", ".") if bq else None, "bucket": gcs,
                         "push_host": ctx.redactor.host(urlparse(push).hostname) if push else None,
                         "dead_letter": bool(s.get("deadLetterPolicy")), "state": s.get("state"),
                         "filter": bool(s.get("filter"))})
        if bq or gcs:
            pipes.append(_record(id=s.get("name"), name=s.get("name", "").split("/")[-1], technology=f"Pub/Sub ({kind} subscription)",
                                 project_id=pid, location="global", sources=[f"pubsub:{s.get('topic', '').split('/')[-1]}"],
                                 targets=[bq.replace(":", ".") if bq else f"gcs:{gcs}"], processing="streaming",
                                 load_strategy="streaming", state=s.get("state"), runs_per_day=None,
                                 frequency="contínua (streaming)", confidence="high",
                                 retry="dead-letter" if s.get("deadLetterPolicy") else None))
    raw = {"topics": [{"name": t.get("name", "").split("/")[-1],
                       "ingestion_source": next(iter((t.get("ingestionDataSourceSettings") or {}).keys()), None),
                       "schema": bool(t.get("schemaSettings")), "retention": t.get("messageRetentionDuration")} for t in topics],
           "subscriptions": out_subs}
    for t in raw["topics"]:
        if t["ingestion_source"]:
            pipes.append(_record(id=f"projects/{pid}/topics/{t['name']}", name=t["name"], technology="Pub/Sub ingestion topic",
                                 project_id=pid, location="global", sources=[f"external:{t['ingestion_source']}"],
                                 targets=[f"pubsub:{t['name']}"], processing="streaming", load_strategy="streaming",
                                 frequency="contínua (streaming)", confidence="high"))
    return pipes, raw


# ---------------------------------------------------------------- Datastream
def _datastream(ctx: Context, pid: str) -> tuple[list, list]:
    if _disabled(ctx, pid, "datastream.googleapis.com", "datastream"):
        return [], []
    pipes, raw = [], []
    for loc in _locations(ctx, pid, "datastream"):
        profiles = {p["name"]: p for p in _try(ctx, "datastream", f"{pid}/{loc}", lambda: list(ctx.gcp.paginate(
            f"{DATASTREAM}/projects/{pid}/locations/{loc}/connectionProfiles", "connectionProfiles", {"pageSize": 100})))}
        streams = _try(ctx, "datastream", f"{pid}/{loc}", lambda: list(ctx.gcp.paginate(
            f"{DATASTREAM}/projects/{pid}/locations/{loc}/streams", "streams", {"pageSize": 100})))
        for s in streams:
            src_cfg = s.get("sourceConfig") or {}
            dst_cfg = s.get("destinationConfig") or {}
            sp = profiles.get(src_cfg.get("sourceConnectionProfile"), {})
            src_type = next((k.replace("Profile", "") for k in sp if k.endswith("Profile")), "desconhecido")
            src_detail = sp.get(f"{src_type}Profile") or {}
            host = src_detail.get("hostname")
            conn = ("private" if sp.get("privateConnectivity") else "ssh-tunnel" if sp.get("forwardSshConnectivity")
                    else "ip-allowlist" if sp.get("staticServiceIpConnectivity") else "desconhecida")
            sc = next((v for k, v in src_cfg.items() if k.endswith("SourceConfig")), {}) or {}
            objects = _count_objects(sc.get("includeObjects") or {})
            bqd = dst_cfg.get("bigqueryDestinationConfig") or {}
            gcsd = dst_cfg.get("gcsDestinationConfig") or {}
            if bqd:
                ds = (bqd.get("singleTargetDataset") or {}).get("datasetId")
                target = f"bigquery:{ds.replace(':', '.')}" if ds else "bigquery:(dataset por schema de origem)"
            elif gcsd:
                target = f"gcs:{gcsd.get('path', '')}"
            else:
                target = "desconhecido"
            pipes.append(_record(id=s.get("name"), name=s.get("displayName") or s.get("name", "").split("/")[-1],
                                 technology="Datastream (CDC)", project_id=pid, location=loc,
                                 sources=[f"external:{src_type}:{ctx.redactor.host(host) or '?'}"], targets=[target],
                                 processing="streaming", load_strategy="CDC", state=s.get("state"),
                                 frequency=f"contínua (freshness {bqd.get('dataFreshness', 'n/d')})", confidence="high",
                                 notes=[f"Conectividade: {conn}", f"Objetos incluídos: {objects or 'todos/n.d.'}"]))
            raw.append({"stream": s.get("name"), "state": s.get("state"), "source_type": src_type,
                        "source_host": ctx.redactor.host(host), "connectivity": conn, "objects_included": objects,
                        "destination": target, "backfill": "all" if s.get("backfillAll") is not None else "none"})
    return pipes, raw


def _count_objects(obj) -> int:
    n = 0
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.endswith("Tables") and isinstance(v, list):
                n += len(v)
            else:
                n += _count_objects(v)
    elif isinstance(obj, list):
        n += sum(_count_objects(x) for x in obj)
    return n


# --------------------------------------------- Scheduler / Workflows / Functions / Run
TARGET_SERVICES = [("workflowexecutions.googleapis.com", "workflows"), ("dataflow.googleapis.com", "dataflow"),
                   ("dataproc.googleapis.com", "dataproc"), ("composer", "composer"), (".run.app", "cloudrun"),
                   ("run.googleapis.com", "cloudrun"), ("cloudfunctions.net", "functions"),
                   ("bigquerydatatransfer.googleapis.com", "bigquery"), ("bigquery.googleapis.com", "bigquery"),
                   ("dataform.googleapis.com", "dataform"), ("appspot.com", "app_engine")]


def _scheduler(ctx: Context, pid: str) -> tuple[list, list]:
    if _disabled(ctx, pid, "cloudscheduler.googleapis.com", "scheduler"):
        return [], []
    locs = _locations(ctx, pid, "scheduler")
    pipes, raw = [], []
    for loc in locs:
        jobs = _try(ctx, "scheduler", f"{pid}/{loc}", lambda: list(ctx.gcp.paginate(
            f"{SCHEDULER}/projects/{pid}/locations/{loc}/jobs", "jobs", {"pageSize": 500})))
        for j in jobs:
            if j.get("pubsubTarget"):
                ttype, svc = "pubsub", f"pubsub:{(j['pubsubTarget'].get('topicName') or '').split('/')[-1]}"
            elif j.get("httpTarget"):
                uri = j["httpTarget"].get("uri") or ""
                svc = next((s for marker, s in TARGET_SERVICES if marker in uri), "http-externo")
                ttype = "http"
            elif j.get("appEngineHttpTarget"):
                ttype, svc = "app_engine", "app_engine"
            else:
                ttype, svc = "desconhecido", "desconhecido"
            retry = j.get("retryConfig") or {}
            rec = _record(id=j.get("name"), name=j.get("name", "").split("/")[-1], technology="Cloud Scheduler",
                          project_id=pid, location=loc, schedule=j.get("schedule"), state=j.get("state"),
                          targets=[svc], retry=retry.get("retryCount"), last_run=j.get("lastAttemptTime"),
                          confidence="high", dependencies=[svc])
            if (j.get("status") or {}).get("code"):
                rec["notes"].append(f"Última execução com erro (code {(j.get('status') or {}).get('code')})")
            pipes.append(rec)
            raw.append({"name": rec["name"], "location": loc, "schedule": j.get("schedule"), "time_zone": j.get("timeZone"),
                        "state": j.get("state"), "target_type": ttype, "target_service": svc})
    return pipes, raw


WORKFLOW_CONNECTORS = re.compile(r"googleapis\.([a-z0-9_]+)\.v\d", re.I)


def _workflows(ctx: Context, pid: str) -> tuple[list, list]:
    if _disabled(ctx, pid, "workflows.googleapis.com", "workflows"):
        return [], []
    wfs = _try(ctx, "workflows", pid, lambda: list(ctx.gcp.paginate(
        f"{WORKFLOWS}/projects/{pid}/locations/-/workflows", "workflows", {"pageSize": 500})))
    pipes, raw = [], []
    for w in wfs:
        connectors = sorted({m.lower() for m in WORKFLOW_CONNECTORS.findall(w.get("sourceContents") or "")})
        execs = _try(ctx, "workflow_executions", w.get("name", ""), lambda: list(ctx.gcp.paginate(
            f"https://workflowexecutions.googleapis.com/v1/{w['name']}/executions", "executions", {"pageSize": 100},
            max_items=100)))
        runs = [{"state": "SUCCEEDED" if e.get("state") == "SUCCEEDED" else e.get("state"), "start": e.get("startTime"),
                 "duration_s": _duration_s(e.get("startTime"), e.get("endTime"))} for e in execs]
        pipes.append(_record(id=w.get("name"), name=w.get("name", "").split("/")[-1], technology="Workflows", project_id=pid,
                             location=w.get("name", "").split("/")[3] if w.get("name") else None, state=w.get("state"),
                             dependencies=connectors, service_account=ctx.redactor.principal(w.get("serviceAccount", "").split("/")[-1]),
                             runs_per_day=round(len(execs) / max(ctx.days, 1), 3) if execs else None,
                             confidence="medium", **_run_stats(runs)))
        raw.append({"name": w.get("name", "").split("/")[-1], "state": w.get("state"), "connectors": connectors,
                    "executions_observed": len(execs)})
    return pipes, raw


def _functions_run(ctx: Context, pid: str) -> tuple[list, dict]:
    pipes, raw = [], {"functions": [], "run_services": [], "run_jobs": []}
    if _enabled(ctx, pid, "cloudfunctions.googleapis.com") is not False:
        fns = _try(ctx, "functions", pid, lambda: list(ctx.gcp.paginate(
            f"{FUNCTIONS}/projects/{pid}/locations/-/functions", "functions", {"pageSize": 500})))
        for f in fns:
            trig = f.get("eventTrigger") or {}
            sc = f.get("serviceConfig") or {}
            raw["functions"].append({"name": f.get("name", "").split("/")[-1], "state": f.get("state"),
                                     "environment": f.get("environment"), "runtime": (f.get("buildConfig") or {}).get("runtime"),
                                     "trigger": trig.get("eventType") or "http", "env_var_count": len(sc.get("environmentVariables") or {}),
                                     "secret_refs": len(sc.get("secretEnvironmentVariables") or []),
                                     "service_account": ctx.redactor.principal(sc.get("serviceAccountEmail"))})
            if trig.get("eventType"):
                pipes.append(_record(id=f.get("name"), name=f.get("name", "").split("/")[-1], technology="Cloud Functions (evento)",
                                     project_id=pid, location=f.get("name", "").split("/")[3], state=f.get("state"),
                                     sources=[f"evento:{trig.get('eventType')}"], processing="event-driven",
                                     frequency="orientada a eventos", confidence="medium",
                                     retry=trig.get("retryPolicy")))
    if _enabled(ctx, pid, "run.googleapis.com") is not False:
        # A API v2 do Cloud Run não aceita o curinga `-` em locations (INVALID_ARGUMENT): lista por região.
        svcs, jobs = [], []
        run_locs = _locations(ctx, pid, "cloudrun")
        if not run_locs:
            ctx.coverage.ok(MODULE, "cloud_run", 0)
        for loc in run_locs:
            svcs += _try(ctx, "cloud_run", f"{pid}/{loc}", lambda loc=loc: list(ctx.gcp.paginate(
                f"{RUN}/projects/{pid}/locations/{loc}/services", "services", {"pageSize": 500})))
            jobs += _try(ctx, "cloud_run", f"{pid}/{loc}", lambda loc=loc: list(ctx.gcp.paginate(
                f"{RUN}/projects/{pid}/locations/{loc}/jobs", "jobs", {"pageSize": 500})))
        for s in svcs:
            raw["run_services"].append({"name": s.get("name", "").split("/")[-1], "ingress": s.get("ingress"),
                                        "images": sorted({(c.get("image") or "").split("@")[0].split(":")[0]
                                                          for c in (s.get("template") or {}).get("containers") or []})})
        for j in jobs:
            raw["run_jobs"].append({"name": j.get("name", "").split("/")[-1], "executions": j.get("executionCount"),
                                    "last_execution": (j.get("latestCreatedExecution") or {}).get("createTime")})
            pipes.append(_record(id=j.get("name"), name=j.get("name", "").split("/")[-1], technology="Cloud Run job",
                                 project_id=pid, location=j.get("name", "").split("/")[3], last_run=(j.get("latestCreatedExecution") or {}).get("createTime"),
                                 confidence="low", notes=[f"execuções totais: {j.get('executionCount')}"]))
    return pipes, raw


def _connections(ctx: Context, pid: str) -> list:
    out = []
    for loc in sorted({loc.lower() for loc in ctx.regions_for(pid)}):
        conns = _try(ctx, "bigquery_connections", f"{pid}/{loc}", lambda: list(ctx.gcp.paginate(
            f"{BQCONN}/projects/{pid}/locations/{loc}/connections", "connections", {"pageSize": 100})))
        for c in conns:
            kind = next((k for k in ("cloudSql", "aws", "azure", "cloudSpanner", "cloudResource", "spark",
                                      "salesforceDataCloud") if c.get(k)), "outro")
            detail = c.get(kind) or {}
            out.append({"name": c.get("name", "").split("/")[-1], "location": loc, "type": kind,
                        "cloudsql_type": detail.get("type") if kind == "cloudSql" else None,
                        "target": detail.get("instanceId") or detail.get("database") or None,
                        "has_credential": c.get("hasCredential"), "last_modified": c.get("lastModifiedTime")})
    return out


# -------------------------------------------------------------------- main
def collect(ctx: Context) -> dict:
    all_pipes: list[dict] = []
    raw: dict = defaultdict(dict)
    dataform_edges: list[dict] = []

    def per_project(pid):
        def tech(key, fn):
            def run():
                t0 = time.monotonic()
                try:
                    p, r = fn(ctx, pid)
                except Exception as exc:  # isolamento por tecnologia
                    log.warning("Falha inesperada em %s/%s: %s", key, pid, exc)
                    ctx.coverage.limitation(MODULE, key, f"{pid}: erro inesperado {type(exc).__name__}")
                    return
                all_pipes.extend(p)
                raw[key][pid] = r
                log.debug("pipelines/%s %s: %.1fs", key, pid, time.monotonic() - t0)
            return run

        def dataform():
            try:
                p, r, e = _dataform(ctx, pid)
                all_pipes.extend(p)
                raw["dataform"][pid] = r
                dataform_edges.extend(e)
            except Exception as exc:
                ctx.coverage.limitation(MODULE, "dataform", f"{pid}: erro inesperado {type(exc).__name__}")

        def connections():
            raw["bigquery_connections"][pid] = _connections(ctx, pid)

        jobs = [tech(k, f) for k, f in [("transfers", _transfers), ("composer", _composer), ("dataflow", _dataflow),
                                         ("dataproc", _dataproc), ("datafusion", _datafusion), ("pubsub", _pubsub),
                                         ("datastream", _datastream), ("scheduler", _scheduler), ("workflows", _workflows),
                                         ("functions_run", _functions_run)]] + [dataform, connections]
        # as tecnologias são independentes entre si; o teto de requisições simultâneas é o semáforo global do ApiClient
        ctx.run_parallel(lambda job: job(), jobs, f"pipelines/{pid}", workers=len(jobs))

    ctx.run_parallel(per_project, ctx.project_ids(), "pipelines")
    all_pipes.sort(key=lambda x: (str(x.get("technology")), str(x.get("project_id")), str(x.get("name"))))
    ctx.save_raw(MODULE, "pipeline_inventory", all_pipes)
    ctx.save_raw(MODULE, "dataform_edges", dataform_edges)
    for k, v in raw.items():
        ctx.save_raw(MODULE, k, v)
    for comp in ["bigquery_transfers", "bigquery_transfer_runs", "composer", "airflow_dags", "airflow_dag_runs",
                 "dataflow", "dataflow_details", "dataproc", "dataproc_jobs", "dataproc_batches", "dataform",
                 "dataform_workflows", "dataform_graph", "datafusion", "datafusion_pipelines", "pubsub", "datastream",
                 "scheduler", "workflows", "workflow_executions", "functions", "cloud_run", "bigquery_connections"]:
        ctx.coverage.finalize(MODULE, comp, source=comp.replace("_", " "), history_window=f"{ctx.days} dias")
    return {"pipelines": len(all_pipes)}
