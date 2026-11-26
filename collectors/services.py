"""Inventário de serviços operacionais e de IA (complementa o item 3).

Cloud SQL, AlloyDB, Spanner, Compute Engine, GKE, Vertex AI (+ Workbench),
Dataplex e Looker. Coleta somente metadados de configuração; metadados de
instância (startup scripts, variáveis) e credenciais nunca são persistidos.
Também avalia prontidão de CDC dos bancos (binlog / logical decoding), relevante
para Lakeflow Connect, Datastream ou federation.
"""
from __future__ import annotations

import re

from core.context import Context
from utils.http import ApiError, NotFound
from utils.logging import get_logger

log = get_logger("services")
MODULE = "services"
SQLADMIN = "https://sqladmin.googleapis.com/v1"
ALLOYDB = "https://alloydb.googleapis.com/v1"
SPANNER = "https://spanner.googleapis.com/v1"
COMPUTE = "https://compute.googleapis.com/compute/v1"
GKE = "https://container.googleapis.com/v1"
NOTEBOOKS = "https://notebooks.googleapis.com/v2"
DATAPLEX = "https://dataplex.googleapis.com/v1"
LOOKER = "https://looker.googleapis.com/v1"


def _enabled(ctx: Context, pid: str, api: str) -> bool | None:
    svcs = (ctx.load_raw("discovery", "services_enabled") or {}).get(pid)
    return None if not svcs else api in svcs


def _asset_locations(ctx: Context, pid: str, service: str) -> list[str] | None:
    assets = (ctx.load_raw("discovery", "assets") or {}).get(pid)
    if assets is None:
        return None
    return sorted({a["location"] for a in assets if a.get("service") == service and a.get("location")
                   and a["location"] != "global"})


def _regional(locs) -> list[str]:
    """Mantém só regiões (ex.: us-central1); descarta multi-regiões do BigQuery (US, EU) que APIs regionais rejeitam."""
    return sorted({loc.lower() for loc in locs if re.fullmatch(r"[a-z]+-[a-z]+[0-9]+", str(loc).lower())})


def _safe(ctx: Context, comp: str, scope: str, fn):
    try:
        out = fn()
        ctx.coverage.ok(MODULE, comp, len(out))
        return out
    except NotFound:
        ctx.coverage.ok(MODULE, comp, 0)
        return []
    except ApiError as exc:
        ctx.coverage.fail(MODULE, comp, exc, scope=scope)
        return []


def _cloudsql(ctx, pid):
    if _enabled(ctx, pid, "sqladmin.googleapis.com") is False:
        ctx.coverage.ok(MODULE, "cloudsql", 0)
        return []
    items = _safe(ctx, "cloudsql", pid, lambda: list(ctx.gcp.paginate(f"{SQLADMIN}/projects/{pid}/instances", "items")))
    out = []
    for i in items:
        st = i.get("settings") or {}
        flags = {f.get("name"): f.get("value") for f in st.get("databaseFlags") or []}
        engine = (i.get("databaseVersion") or "").split("_")[0]
        backup = st.get("backupConfiguration") or {}
        cdc = None
        if engine == "MYSQL":
            cdc = bool(backup.get("binaryLogEnabled"))
        elif engine == "POSTGRES":
            cdc = flags.get("cloudsql.logical_decoding") == "on"
        elif engine == "SQLSERVER":
            cdc = None  # CDC é habilitado por banco (sys.sp_cdc_enable_db); não visível pela Admin API
        out.append({"name": i.get("name"), "engine": engine, "version": i.get("databaseVersion"), "region": i.get("region"),
                    "state": i.get("state"), "tier": st.get("tier"), "availability": st.get("availabilityType"),
                    "disk_gb": int(st.get("dataDiskSizeGb") or 0), "public_ip": (st.get("ipConfiguration") or {}).get("ipv4Enabled"),
                    "private_network": bool((st.get("ipConfiguration") or {}).get("privateNetwork")),
                    "instance_type": i.get("instanceType"), "replicas": len(i.get("replicaNames") or []),
                    "pitr": backup.get("pointInTimeRecoveryEnabled"), "cdc_ready": cdc})
    return out


def _alloydb(ctx, pid):
    if _enabled(ctx, pid, "alloydb.googleapis.com") is False:
        ctx.coverage.ok(MODULE, "alloydb", 0)
        return []
    cl = _safe(ctx, "alloydb", pid, lambda: list(ctx.gcp.paginate(f"{ALLOYDB}/projects/{pid}/locations/-/clusters", "clusters")))
    return [{"name": c.get("name", "").split("/")[-1], "location": c.get("name", "").split("/")[3], "state": c.get("state"),
             "version": c.get("databaseVersion"), "type": c.get("clusterType")} for c in cl]


def _spanner(ctx, pid):
    if _enabled(ctx, pid, "spanner.googleapis.com") is False:
        ctx.coverage.ok(MODULE, "spanner", 0)
        return []
    ins = _safe(ctx, "spanner", pid, lambda: list(ctx.gcp.paginate(f"{SPANNER}/projects/{pid}/instances", "instances")))
    return [{"name": i.get("name", "").split("/")[-1], "config": (i.get("config") or "").split("/")[-1],
             "nodes": i.get("nodeCount"), "processing_units": i.get("processingUnits"), "state": i.get("state"),
             "edition": i.get("edition")} for i in ins]


def _compute(ctx, pid):
    if _enabled(ctx, pid, "compute.googleapis.com") is False:
        ctx.coverage.ok(MODULE, "compute", 0)
        return []

    def fetch():
        out = []
        for zone, vm in ctx.gcp.paginate_aggregated(f"{COMPUTE}/projects/{pid}/aggregated/instances", "instances"):
            out.append({"name": vm.get("name"), "zone": zone.replace("zones/", ""),
                        "machine_type": (vm.get("machineType") or "").split("/")[-1], "status": vm.get("status"),
                        "labels": vm.get("labels") or {}, "created": vm.get("creationTimestamp"),
                        "os_hint": next((lic.split("/")[-1] for d in vm.get("disks") or [] for lic in d.get("licenses") or []), None),
                        "gpus": sum(int(a.get("acceleratorCount") or 0) for a in vm.get("guestAccelerators") or []),
                        "service_account": ctx.redactor.principal(next((s.get("email") for s in vm.get("serviceAccounts") or []), None))})
        return out
    return _safe(ctx, "compute", pid, fetch)


def _gke(ctx, pid):
    if _enabled(ctx, pid, "container.googleapis.com") is False:
        ctx.coverage.ok(MODULE, "gke", 0)
        return []
    def fetch():
        return ctx.gcp.get(f"{GKE}/projects/{pid}/locations/-/clusters").get("clusters") or []
    cl = _safe(ctx, "gke", pid, fetch)
    return [{"name": c.get("name"), "location": c.get("location"), "status": c.get("status"),
             "nodes": c.get("currentNodeCount"), "autopilot": (c.get("autopilot") or {}).get("enabled", False),
             "version": c.get("currentMasterVersion")} for c in cl]


def _vertex(ctx, pid):
    if _enabled(ctx, pid, "aiplatform.googleapis.com") is False:
        ctx.coverage.ok(MODULE, "vertex_ai", 0)
        return {}
    locs = _asset_locations(ctx, pid, "vertex")
    if locs is None:
        locs = sorted({r for r in ctx.config["gcp"].get("regions") or []})
        if not locs:
            ctx.coverage.limitation(MODULE, "vertex_ai", f"{pid}: sem Asset Inventory nem gcp.regions; Vertex AI não listado")
            return {}
    out = {"endpoints": [], "models": 0, "pipeline_jobs": 0, "workbench": []}
    for loc in locs:
        base = f"https://{loc}-aiplatform.googleapis.com/v1/projects/{pid}/locations/{loc}"
        eps = _safe(ctx, "vertex_ai", f"{pid}/{loc}", lambda: list(ctx.gcp.paginate(f"{base}/endpoints", "endpoints")))
        out["endpoints"] += [{"name": e.get("displayName"), "location": loc,
                              "deployed_models": len(e.get("deployedModels") or [])} for e in eps]
        out["models"] += len(_safe(ctx, "vertex_ai", f"{pid}/{loc}", lambda: list(ctx.gcp.paginate(
            f"{base}/models", "models", {"pageSize": 100}, max_items=2000))))
        out["pipeline_jobs"] += len(_safe(ctx, "vertex_ai", f"{pid}/{loc}", lambda: list(ctx.gcp.paginate(
            f"{base}/pipelineJobs", "pipelineJobs", {"pageSize": 100}, max_items=2000))))
    if _enabled(ctx, pid, "notebooks.googleapis.com") is not False:
        wb = _safe(ctx, "vertex_ai", pid, lambda: list(ctx.gcp.paginate(f"{NOTEBOOKS}/projects/{pid}/locations/-/instances", "instances")))
        out["workbench"] = [{"name": w.get("name", "").split("/")[-1], "state": w.get("state")} for w in wb]
    return out


def _dataplex(ctx, pid):
    if _enabled(ctx, pid, "dataplex.googleapis.com") is False:
        ctx.coverage.ok(MODULE, "dataplex", 0)
        return {}
    locs = _asset_locations(ctx, pid, "dataplex")
    if not locs:
        locs = _regional(ctx.regions_for(pid))
    out = {"lakes": [], "data_scans": []}
    for loc in locs:
        base = f"{DATAPLEX}/projects/{pid}/locations/{loc}"
        out["lakes"] += [{"name": lk.get("name", "").split("/")[-1], "location": loc, "state": lk.get("state")}
                         for lk in _safe(ctx, "dataplex", f"{pid}/{loc}", lambda: list(ctx.gcp.paginate(f"{base}/lakes", "lakes")))]
        out["data_scans"] += [{"name": d.get("name", "").split("/")[-1], "location": loc, "type": d.get("type"),
                               "state": d.get("state")}
                              for d in _safe(ctx, "dataplex", f"{pid}/{loc}", lambda: list(ctx.gcp.paginate(f"{base}/dataScans", "dataScans")))]
    return out


def _looker(ctx, pid):
    if _enabled(ctx, pid, "looker.googleapis.com") is False:
        ctx.coverage.ok(MODULE, "looker", 0)
        return []
    locs = _asset_locations(ctx, pid, "looker") or []
    out = []
    for loc in locs:
        out += [{"name": i.get("name", "").split("/")[-1], "location": loc, "state": i.get("state"),
                 "edition": i.get("platformEdition")}
                for i in _safe(ctx, "looker", f"{pid}/{loc}", lambda: list(ctx.gcp.paginate(
                    f"{LOOKER}/projects/{pid}/locations/{loc}/instances", "instances")))]
    return out


def collect(ctx: Context) -> dict:
    result: dict = {}

    def per(pid):
        fns = {"cloudsql": _cloudsql, "alloydb": _alloydb, "spanner": _spanner, "compute": _compute, "gke": _gke,
               "vertex_ai": _vertex, "dataplex": _dataplex, "looker": _looker}
        out: dict = {}

        def one(item):
            out[item[0]] = item[1](ctx, pid)

        ctx.run_parallel(one, list(fns.items()), f"services/{pid}", workers=len(fns))
        result[pid] = {k: out.get(k) for k in fns}

    ctx.run_parallel(per, ctx.project_ids(), "services")
    ctx.save_raw(MODULE, "services", result)
    for comp, src in [("cloudsql", "Cloud SQL Admin API"), ("alloydb", "AlloyDB API"), ("spanner", "Spanner API"),
                      ("compute", "Compute Engine aggregated instances"), ("gke", "GKE API"),
                      ("vertex_ai", "Vertex AI / Notebooks API"), ("dataplex", "Dataplex API"), ("looker", "Looker (Google Cloud core) API")]:
        ctx.coverage.finalize(MODULE, comp, source=src)
    return {"projects": len(result)}
