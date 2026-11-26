"""Discovery da infraestrutura GCP (item 3).

Fontes: Cloud Resource Manager v3, Service Usage, Cloud Asset Inventory,
Cloud Billing, IAM, Compute (rede), BigQuery (localização dos datasets) e
Cloud Monitoring (métrica serviceruntime de chamadas de API = evidência de uso).
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from collectors.service_catalog import ALL_ASSET_TYPES, ASSET_TO_SERVICE
from core.context import Context, matches_any
from utils.http import ApiError
from utils.logging import get_logger

log = get_logger("discovery")
MODULE = "discovery"
CRM = "https://cloudresourcemanager.googleapis.com/v3"
SU = "https://serviceusage.googleapis.com/v1"
CAI = "https://cloudasset.googleapis.com/v1"
BILLING = "https://cloudbilling.googleapis.com/v1"
IAM = "https://iam.googleapis.com/v1"
COMPUTE = "https://compute.googleapis.com/compute/v1"
BQ = "https://bigquery.googleapis.com/bigquery/v2"
MON = "https://monitoring.googleapis.com/v3"

ENV_PATTERNS = [
    ("prod", r"(^|[^a-z0-9])(prd|prod|production|producao|prod\d)([^a-z0-9]|$)"),
    ("hml", r"(^|[^a-z0-9])(hml|homolog|homologacao|stg|stage|staging|uat|qa|pre|preprod)([^a-z0-9]|$)"),
    ("dev", r"(^|[^a-z0-9])(dev|desenv|develop|development|sbx|sandbox|test|tst|lab|poc)([^a-z0-9]|$)"),
]


def infer_environment(project: dict) -> str:
    labels = project.get("labels") or {}
    for key in ("env", "environment", "ambiente", "stage"):
        if labels.get(key):
            return str(labels[key]).lower()
    text = " ".join(x for x in (project.get("project_id"), project.get("display_name")) if x).lower()
    for env, pat in ENV_PATTERNS:
        if re.search(pat, text):
            return env
    return "unknown"


# ------------------------------------------------------------------ projects
def resolve_projects(ctx: Context, with_bq_locations: bool = True, use_cache: bool = False) -> list[dict]:
    """Descobre e filtra projetos. Usado por todos os módulos (discovery 'leve')."""
    if ctx.projects:
        return ctx.projects
    cached = ctx.load_raw(MODULE, "projects") if use_cache else None
    if cached:
        ctx.projects = cached
        return cached

    gcfg = ctx.config["gcp"]
    cov = ctx.coverage
    projects: list[dict] = []
    wanted = [p.strip() for p in gcfg.get("projects") or [] if p.strip()]
    if wanted:
        for pid in wanted:
            try:
                p = ctx.gcp.get(f"{CRM}/projects/{pid}")
                projects.append(_norm_project(p))
                cov.ok(MODULE, "projects", 1)
            except ApiError as exc:
                cov.fail(MODULE, "projects", exc, scope=pid)
                projects.append({"project_id": pid, "project_number": None, "state": "UNKNOWN",
                                 "display_name": pid, "parent": None, "labels": {}, "metadata_error": exc.kind})
    else:
        # projects.search retorna todos os projetos visíveis ao principal (em qualquer organização/folder).
        query = "state:ACTIVE"
        try:
            for p in ctx.gcp.paginate(f"{CRM}/projects:search", "projects", {"query": query, "pageSize": 500},
                                      max_items=gcfg.get("max_projects", 500)):
                projects.append(_norm_project(p))
            cov.ok(MODULE, "projects", len(projects))
        except ApiError as exc:
            cov.fail(MODULE, "projects", exc, scope="projects:search")

    excl = set(gcfg.get("exclude_projects") or [])
    pats = gcfg.get("exclude_project_patterns") or []
    for p in projects:
        p["selected"] = (p.get("state") in ("ACTIVE", "UNKNOWN")) and p["project_id"] not in excl and \
            not matches_any(p["project_id"], pats)
        p["environment"] = infer_environment(p)

    if with_bq_locations:
        def locs(p):
            if not p["selected"]:
                return
            try:
                datasets = list(ctx.gcp.paginate(f"{BQ}/projects/{p['project_id']}/datasets", "datasets",
                                                 {"all": "true", "maxResults": 1000}))
                p["bigquery_dataset_count"] = len(datasets)
                p["bigquery_locations"] = sorted({d.get("location") for d in datasets if d.get("location")})
                ctx.save_raw("bigquery", f"datasets_list__{p['project_id']}", datasets)
                cov.ok(MODULE, "bigquery_locations", len(datasets))
            except ApiError as exc:
                p["bigquery_locations"] = []
                p["bigquery_dataset_count"] = None
                cov.fail(MODULE, "bigquery_locations", exc, scope=p["project_id"])

        ctx.run_parallel(locs, projects, "bigquery_locations")
        cov.finalize(MODULE, "bigquery_locations", source="BigQuery API datasets.list")

    cov.finalize(MODULE, "projects", source="Cloud Resource Manager v3 projects.search/get")
    ctx.projects = projects
    ctx.save_raw(MODULE, "projects", projects)
    log.info("Projetos descobertos: %s (selecionados: %s)", len(projects), sum(p["selected"] for p in projects))
    return projects


def _norm_project(p: dict) -> dict:
    return {
        "project_id": p.get("projectId"),
        "project_number": (p.get("name") or "").split("/")[-1] or None,
        "display_name": p.get("displayName"),
        "state": p.get("state"),
        "parent": p.get("parent"),
        "labels": p.get("labels") or {},
        "create_time": p.get("createTime"),
    }


# --------------------------------------------------------------------- full
def collect(ctx: Context) -> dict:
    cov = ctx.coverage
    projects = resolve_projects(ctx)
    pids = ctx.project_ids()

    # Organização e folders (podem não estar visíveis; não é erro fatal)
    orgs, folders = [], []
    try:
        orgs = list(ctx.gcp.paginate(f"{CRM}/organizations:search", "organizations", {"pageSize": 100}))
        cov.ok(MODULE, "organizations", len(orgs))
    except ApiError as exc:
        cov.fail(MODULE, "organizations", exc, scope="organizations:search")
    try:
        folders = list(ctx.gcp.paginate(f"{CRM}/folders:search", "folders", {"pageSize": 500}, max_items=5000))
        cov.ok(MODULE, "folders", len(folders))
    except ApiError as exc:
        cov.fail(MODULE, "folders", exc, scope="folders:search")
    ctx.save_raw(MODULE, "organizations", [{"name": o.get("name"), "display_name": o.get("displayName"),
                                            "state": o.get("state")} for o in orgs])
    ctx.save_raw(MODULE, "folders", [{"name": f.get("name"), "display_name": f.get("displayName"),
                                      "parent": f.get("parent"), "state": f.get("state")} for f in folders])

    enabled: dict[str, list[str]] = {}
    assets: dict[str, list[dict]] = {}
    billing_info: dict[str, dict] = {}
    service_accounts: dict[str, list[dict]] = {}
    networks: dict[str, dict] = {}
    api_usage: dict[str, dict] = {}

    def per_project(pid: str):
        enabled[pid] = _enabled_services(ctx, pid)
        assets[pid] = _assets(ctx, pid)
        billing_info[pid] = _billing_info(ctx, pid)
        service_accounts[pid] = _service_accounts(ctx, pid)
        networks[pid] = _network(ctx, pid, enabled[pid])
        api_usage[pid] = _api_usage(ctx, pid)

    ctx.run_parallel(per_project, pids, "discovery")

    billing_accounts = []
    try:
        billing_accounts = [{"name": b.get("name"), "display_name": b.get("displayName"), "open": b.get("open"),
                             "master_billing_account": b.get("masterBillingAccount")}
                            for b in ctx.gcp.paginate(f"{BILLING}/billingAccounts", "billingAccounts")]
        cov.ok(MODULE, "billing_accounts", len(billing_accounts))
    except ApiError as exc:
        cov.fail(MODULE, "billing_accounts", exc, scope="billingAccounts.list")

    ctx.save_raw(MODULE, "services_enabled", enabled)
    ctx.save_raw(MODULE, "assets", assets)
    ctx.save_raw(MODULE, "billing_info", billing_info)
    ctx.save_raw(MODULE, "billing_accounts", billing_accounts)
    ctx.save_raw(MODULE, "service_accounts", service_accounts)
    ctx.save_raw(MODULE, "networks", networks)
    ctx.save_raw(MODULE, "api_usage", api_usage)

    for comp, src in [("organizations", "CRM v3 organizations.search"), ("folders", "CRM v3 folders.search"),
                      ("services_enabled", "Service Usage services.list"), ("assets", "Cloud Asset Inventory searchAllResources"),
                      ("billing_info", "Cloud Billing projects.getBillingInfo"), ("billing_accounts", "Cloud Billing billingAccounts.list"),
                      ("service_accounts", "IAM serviceAccounts.list"), ("network", "Compute networks/subnetworks/VPN/Interconnect"),
                      ("api_usage", "Cloud Monitoring serviceruntime.googleapis.com/api/request_count")]:
        cov.finalize(MODULE, comp, source=src)
    cov.limitation(MODULE, "api_usage", "A métrica consumed_api atribui chamadas ao projeto consumidor; chamadas "
                                        "desta própria ferramenta aparecem no quota project.")
    return {"projects": len(projects), "selected": len(pids)}


def _enabled_services(ctx: Context, pid: str) -> list[str]:
    try:
        svcs = [s.get("config", {}).get("name") or s.get("name", "").split("/")[-1]
                for s in ctx.gcp.paginate(f"{SU}/projects/{pid}/services", "services",
                                          {"filter": "state:ENABLED", "pageSize": 200})]
        ctx.coverage.ok(MODULE, "services_enabled", len(svcs))
        return sorted(svcs)
    except ApiError as exc:
        ctx.coverage.fail(MODULE, "services_enabled", exc, scope=pid)
        return []


def _assets(ctx: Context, pid: str) -> list[dict]:
    """searchAllResources com os tipos do catálogo; se algum tipo for rejeitado, tenta individualmente."""
    max_items = ctx.config["gcp"].get("max_assets_per_project", 20000)
    url = f"{CAI}/projects/{pid}:searchAllResources"
    fields = ("results.name,results.assetType,results.location,results.state,results.labels,results.createTime,"
              "results.updateTime,results.displayName,nextPageToken")

    def norm(r):
        return {"name": r.get("name"), "asset_type": r.get("assetType"), "location": r.get("location"),
                "state": r.get("state"), "labels": r.get("labels") or {}, "create_time": r.get("createTime"),
                "update_time": r.get("updateTime"), "display_name": r.get("displayName"),
                "service": ASSET_TO_SERVICE.get(r.get("assetType"))}

    try:
        out = [norm(r) for r in ctx.gcp.paginate(url, "results", {"assetTypes": ALL_ASSET_TYPES, "pageSize": 500,
                                                                  "fields": fields}, max_items=max_items)]
        if len(out) >= max_items:
            ctx.coverage.limitation(MODULE, "assets", f"{pid}: limite de {max_items} assets atingido (amostra)")
        ctx.coverage.ok(MODULE, "assets", len(out))
        return out
    except ApiError as exc:
        if exc.status != 400:
            ctx.coverage.fail(MODULE, "assets", exc, scope=pid)
            return []
    out, rejected = [], []
    for t in ALL_ASSET_TYPES:
        try:
            out.extend(norm(r) for r in ctx.gcp.paginate(url, "results", {"assetTypes": [t], "pageSize": 500,
                                                                          "fields": fields}, max_items=max_items))
        except ApiError as exc:
            if exc.status == 400:
                rejected.append(t)
            else:
                ctx.coverage.fail(MODULE, "assets", exc, scope=f"{pid}/{t}")
                return out
    if rejected:
        ctx.coverage.limitation(MODULE, "assets", f"Tipos não suportados pelo Asset Inventory: {', '.join(rejected)}")
    ctx.coverage.ok(MODULE, "assets", len(out))
    return out


def _billing_info(ctx: Context, pid: str) -> dict:
    try:
        b = ctx.gcp.get(f"{BILLING}/projects/{pid}/billingInfo")
        ctx.coverage.ok(MODULE, "billing_info", 1)
        return {"billing_account": b.get("billingAccountName"), "billing_enabled": b.get("billingEnabled")}
    except ApiError as exc:
        ctx.coverage.fail(MODULE, "billing_info", exc, scope=pid)
        return {}


def _service_accounts(ctx: Context, pid: str) -> list[dict]:
    try:
        red = ctx.redactor
        out = [{"email": red.principal(a.get("email")).split(":", 1)[-1] if red.anonymize else a.get("email"),
                "display_name": None if red.anonymize else a.get("displayName"), "disabled": a.get("disabled", False),
                "user_managed": not (a.get("email") or "").split("@")[-1].startswith(("gcp-sa-", "cloudservices"))}
               for a in ctx.gcp.paginate(f"{IAM}/projects/{pid}/serviceAccounts", "accounts", {"pageSize": 100})]
        ctx.coverage.ok(MODULE, "service_accounts", len(out))
        return out
    except ApiError as exc:
        ctx.coverage.fail(MODULE, "service_accounts", exc, scope=pid)
        return []


def _network(ctx: Context, pid: str, enabled: list[str]) -> dict:
    if enabled and "compute.googleapis.com" not in enabled:
        return {"compute_api_enabled": False}
    out: dict = {"compute_api_enabled": True}
    try:
        nets = list(ctx.gcp.paginate(f"{COMPUTE}/projects/{pid}/global/networks", "items"))
        out["networks"] = [{"name": n.get("name"), "auto_create_subnetworks": n.get("autoCreateSubnetworks"),
                            "subnet_count": len(n.get("subnetworks") or []),
                            "routing_mode": (n.get("routingConfig") or {}).get("routingMode"),
                            "peerings": [{"name": pe.get("name"), "network": pe.get("network", "").split("/projects/")[-1],
                                          "state": pe.get("state")} for pe in n.get("peerings") or []]}
                           for n in nets]
        subnets = [s for _, s in ctx.gcp.paginate_aggregated(f"{COMPUTE}/projects/{pid}/aggregated/subnetworks",
                                                             "subnetworks")]
        out["subnetworks"] = [{"name": s.get("name"), "region": (s.get("region") or "").split("/")[-1],
                               "network": (s.get("network") or "").split("/")[-1],
                               "private_google_access": s.get("privateIpGoogleAccess"),
                               "purpose": s.get("purpose")} for s in subnets]
        out["vpn_tunnels"] = [{"name": t.get("name"), "region": (t.get("region") or "").split("/")[-1],
                               "status": t.get("status")}
                              for _, t in ctx.gcp.paginate_aggregated(f"{COMPUTE}/projects/{pid}/aggregated/vpnTunnels",
                                                                      "vpnTunnels")]
        out["interconnect_attachments"] = [
            {"name": a.get("name"), "region": (a.get("region") or "").split("/")[-1], "type": a.get("type"),
             "state": a.get("state")}
            for _, a in ctx.gcp.paginate_aggregated(f"{COMPUTE}/projects/{pid}/aggregated/interconnectAttachments",
                                                    "interconnectAttachments")]
        routers = [r for _, r in ctx.gcp.paginate_aggregated(f"{COMPUTE}/projects/{pid}/aggregated/routers", "routers")]
        out["cloud_nat_count"] = sum(len(r.get("nats") or []) for r in routers)
        try:
            proj = ctx.gcp.get(f"{COMPUTE}/projects/{pid}", params={"fields": "xpnProjectStatus"})
            out["shared_vpc_host"] = proj.get("xpnProjectStatus") == "HOST"
            host = ctx.gcp.get(f"{COMPUTE}/projects/{pid}/getXpnHost", params={"fields": "name"})
            out["shared_vpc_host_project"] = host.get("name")
        except ApiError:
            pass
        out["private_services_access"] = any("servicenetworking" in (pe.get("network") or "")
                                             for n in out["networks"] for pe in n["peerings"])
        ctx.coverage.ok(MODULE, "network", len(out["networks"]))
    except ApiError as exc:
        ctx.coverage.fail(MODULE, "network", exc, scope=pid)
    return out


def _api_usage(ctx: Context, pid: str) -> dict:
    """Total de chamadas por API na janela (evidência de uso efetivo)."""
    end = datetime.now(timezone.utc)
    days = min(ctx.days, 180)
    start = end - timedelta(days=days)
    params = {
        "filter": 'metric.type="serviceruntime.googleapis.com/api/request_count" AND resource.type="consumed_api"',
        "interval.startTime": start.isoformat().replace("+00:00", "Z"),
        "interval.endTime": end.isoformat().replace("+00:00", "Z"),
        "aggregation.alignmentPeriod": f"{days * 86400}s",
        "aggregation.perSeriesAligner": "ALIGN_SUM",
        "aggregation.crossSeriesReducer": "REDUCE_SUM",
        "aggregation.groupByFields": "resource.label.service",
        "view": "FULL",
    }
    try:
        counts: Counter = Counter()
        for ts in ctx.gcp.paginate(f"{MON}/projects/{pid}/timeSeries", "timeSeries", params):
            svc = ((ts.get("resource") or {}).get("labels") or {}).get("service")
            for pt in ts.get("points") or []:
                v = pt.get("value") or {}
                counts[svc] += int(v.get("int64Value") or v.get("doubleValue") or 0)
        ctx.coverage.ok(MODULE, "api_usage", len(counts))
        return {"window_days": days, "requests_by_api": dict(counts)}
    except ApiError as exc:
        ctx.coverage.fail(MODULE, "api_usage", exc, scope=pid)
        return {}


def summarize_assets(assets: dict[str, list[dict]]) -> dict:
    by_service: dict = defaultdict(lambda: {"count": 0, "projects": set(), "locations": Counter(), "types": Counter()})
    for pid, items in (assets or {}).items():
        for a in items:
            key = a.get("service") or a.get("asset_type")
            s = by_service[key]
            s["count"] += 1
            s["projects"].add(pid)
            s["locations"][a.get("location") or "global"] += 1
            s["types"][a.get("asset_type")] += 1
    return {k: {"count": v["count"], "projects": sorted(v["projects"]), "locations": dict(v["locations"]),
                "types": dict(v["types"])} for k, v in by_service.items()}
