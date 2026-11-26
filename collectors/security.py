"""Segurança e governança — somente metadados (item 13).

IAM (políticas de projeto + auditConfigs), chaves de service accounts
(metadados; material de chave nunca é retornado pela API), ACL/CMEK de datasets,
VPC Service Controls, Organization Policies efetivas, policy tags (Data
Catalog), políticas de data masking (BigQuery Data Policy API) e perfis de dados
existentes do Sensitive Data Protection (DLP). Nenhum dado de negócio é lido;
classificação usa apenas metadados e classificações já existentes.
Membros IAM são pseudonimizados; apenas tipo e domínio são preservados.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from core.context import Context
from utils.http import ApiError, NotFound
from utils.logging import get_logger

log = get_logger("security")
MODULE = "security"
CRM = "https://cloudresourcemanager.googleapis.com/v3"
IAM = "https://iam.googleapis.com/v1"
BQ = "https://bigquery.googleapis.com/bigquery/v2"
ACM = "https://accesscontextmanager.googleapis.com/v1"
ORGPOL = "https://orgpolicy.googleapis.com/v2"
DATACATALOG = "https://datacatalog.googleapis.com/v1"
DATAPOLICY = "https://bigquerydatapolicy.googleapis.com/v1"
DLP = "https://dlp.googleapis.com/v2"
KEY_CONSTRAINTS = ["iam.allowedPolicyMemberDomains", "gcp.resourceLocations", "storage.publicAccessPrevention",
                   "iam.disableServiceAccountKeyCreation", "storage.uniformBucketLevelAccess", "sql.restrictPublicIp",
                   "compute.vmExternalIpAccess", "gcp.restrictNonCmekServices", "iam.automaticIamGrantsForDefaultServiceAccounts"]


def _member(ctx: Context, m: str) -> dict:
    kind, _, ident = m.partition(":")
    if not ident:
        kind, ident = ("special", m)
    if kind.startswith("deleted"):
        kind = "deleted"
    domain = ident.split("@", 1)[1].lower() if "@" in ident else (ident if kind == "domain" else None)
    return {"type": kind, "domain": domain, "id": ctx.redactor.principal(m) if ctx.redactor.anonymize else m,
            "public": m in ("allUsers", "allAuthenticatedUsers"),
            "google_managed": bool(domain and (domain.startswith("gcp-sa-") or domain.endswith("cloudservices.gserviceaccount.com")
                                               or domain in ("cloudbuild.gserviceaccount.com", "appspot.gserviceaccount.com"))),
            "default_compute_sa": ident.endswith("-compute@developer.gserviceaccount.com")}


def _iam(ctx: Context, pid: str) -> dict:
    try:
        pol = ctx.gcp.post(f"{CRM}/projects/{pid}:getIamPolicy", json_body={"options": {"requestedPolicyVersion": 3}})
        ctx.coverage.ok(MODULE, "iam_policies", len(pol.get("bindings") or []))
    except ApiError as exc:
        ctx.coverage.fail(MODULE, "iam_policies", exc, scope=pid, permission_hint="roles/iam.securityReviewer")
        return {}
    bindings = [{"role": b.get("role"), "conditional": bool(b.get("condition")),
                 "members": [_member(ctx, m) for m in b.get("members") or []]} for b in pol.get("bindings") or []]
    audit = [{"service": a.get("service"), "log_types": sorted({c.get("logType") for c in a.get("auditLogConfigs") or []}),
              "exempted_members": sum(len(c.get("exemptedMembers") or []) for c in a.get("auditLogConfigs") or [])}
             for a in pol.get("auditConfigs") or []]
    return {"bindings": bindings, "audit_configs": audit}


def _sa_keys(ctx: Context, pid: str) -> list[dict]:
    # Lista ao vivo: o raw do discovery guarda e-mails pseudonimizados, inutilizáveis para chamar a API.
    try:
        sas = [{"email": a.get("email"), "disabled": a.get("disabled")}
               for a in ctx.gcp.paginate(f"{IAM}/projects/{pid}/serviceAccounts", "accounts", {"pageSize": 100})]
    except ApiError as exc:
        ctx.coverage.fail(MODULE, "service_account_keys", exc, scope=pid)
        return []
    now = datetime.now(timezone.utc)

    def one(sa) -> list[dict]:
        email = sa.get("email")
        try:
            keys = ctx.gcp.get(f"{IAM}/projects/{pid}/serviceAccounts/{email}/keys",
                               params={"keyTypes": "USER_MANAGED"}).get("keys") or []
            ctx.coverage.ok(MODULE, "service_account_keys", len(keys))
        except ApiError as exc:
            ctx.coverage.fail(MODULE, "service_account_keys", exc, scope=f"{pid}/sa")
            return []
        out = []
        for k in keys:
            start = k.get("validAfterTime")
            age = None
            if start:
                try:
                    age = (now - datetime.fromisoformat(start.replace("Z", "+00:00"))).days
                except ValueError:
                    pass
            out.append({"service_account": ctx.redactor.principal(email), "key_id_hash": ctx.redactor.pseudonym(k.get("name"), "key"),
                        "age_days": age, "disabled": k.get("disabled", False), "origin": k.get("keyOrigin"),
                        "algorithm": k.get("keyAlgorithm")})
        return out

    return [k for part in ctx.run_parallel(one, sas, f"sa_keys/{pid}", workers=ctx.io_workers) for k in part]


def _dataset_entry(ctx: Context, pid: str, d: dict) -> dict | None:
    ds = (d.get("datasetReference") or {}).get("datasetId")
    try:
        meta = ctx.gcp.get(f"{BQ}/projects/{pid}/datasets/{ds}")
        ctx.coverage.ok(MODULE, "dataset_acl", 1)
    except NotFound:
        return None
    except ApiError as exc:
        ctx.coverage.fail(MODULE, "dataset_acl", exc, scope=f"{pid}.{ds}")
        return None
    entries = []
    for a in meta.get("access") or []:
        if a.get("specialGroup"):
            who = {"type": "specialGroup", "domain": None, "id": a["specialGroup"],
                   "public": a["specialGroup"] == "allAuthenticatedUsers"}
        elif a.get("iamMember"):
            who = _member(ctx, a["iamMember"])
        elif a.get("userByEmail"):
            who = _member(ctx, ("serviceAccount:" if a["userByEmail"].endswith("gserviceaccount.com") else "user:") + a["userByEmail"])
        elif a.get("groupByEmail"):
            who = _member(ctx, "group:" + a["groupByEmail"])
        elif a.get("domain"):
            who = {"type": "domain", "domain": a["domain"], "id": a["domain"], "public": False}
        elif a.get("view") or a.get("routine") or a.get("dataset"):
            who = {"type": "authorized_" + next(k for k in ("view", "routine", "dataset") if a.get(k)), "domain": None,
                   "id": None, "public": False}
        else:
            continue
        entries.append({"role": a.get("role"), **who})
    return {"dataset": f"{pid}.{ds}", "location": meta.get("location"),
            "cmek": bool((meta.get("defaultEncryptionConfiguration") or {}).get("kmsKeyName")),
            "default_table_expiration_ms": meta.get("defaultTableExpirationMs"),
            "max_time_travel_hours": meta.get("maxTimeTravelHours"),
            "storage_billing_model": meta.get("storageBillingModel"), "access": entries,
            "labels": meta.get("labels") or {}}


def _datasets(ctx: Context, pid: str, cap: int) -> list[dict]:
    listed = ctx.load_raw("bigquery", f"datasets_list__{pid}") or []
    res = ctx.run_parallel(lambda d: _dataset_entry(ctx, pid, d), listed[:cap], f"dataset_acl/{pid}", workers=ctx.io_workers)
    out = sorted((r for r in res if r), key=lambda r: r["dataset"])
    if len(listed) > cap:
        ctx.coverage.limitation(MODULE, "dataset_acl", f"{pid}: ACL avaliada em {cap} de {len(listed)} datasets")
    return out


def _org_policies(ctx: Context, pid: str) -> dict:
    out = {}
    for c in KEY_CONSTRAINTS:
        try:
            pol = ctx.gcp.get(f"{ORGPOL}/projects/{pid}/policies/{c}:getEffectivePolicy")
            rules = (pol.get("spec") or {}).get("rules") or []
            out[c] = {"enforced": any(r.get("enforce") for r in rules),
                      "allowed_values": sorted({v for r in rules for v in (r.get("values") or {}).get("allowedValues") or []}),
                      "deny_all": any(r.get("denyAll") for r in rules), "allow_all": any(r.get("allowAll") for r in rules),
                      "rules": len(rules)}
            ctx.coverage.ok(MODULE, "org_policies", 1)
        except ApiError as exc:
            ctx.coverage.fail(MODULE, "org_policies", exc, scope=f"{pid}/{c}")
            if exc.kind in ("permission_denied", "api_disabled"):
                break
    return out


def _governance_loc(ctx: Context, pid: str, loc: str, regional: bool) -> dict:
    out = {"taxonomies": [], "data_policies": [], "dlp_table_profiles": []}
    if regional:
        try:
            for t in ctx.gcp.paginate(f"{DATACATALOG}/projects/{pid}/locations/{loc}/taxonomies", "taxonomies"):
                out["taxonomies"].append({"name": t.get("displayName"), "location": loc,
                                          "policy_tags": t.get("policyTagCount"),
                                          "fine_grained_access": "FINE_GRAINED_ACCESS_CONTROL" in (t.get("activatedPolicyTypes") or [])})
            ctx.coverage.ok(MODULE, "policy_tags", len(out["taxonomies"]))
        except ApiError as exc:
            ctx.coverage.fail(MODULE, "policy_tags", exc, scope=f"{pid}/{loc}")
        try:
            for d in ctx.gcp.paginate(f"{DATAPOLICY}/projects/{pid}/locations/{loc}/dataPolicies", "dataPolicies"):
                out["data_policies"].append({"type": d.get("dataPolicyType"), "location": loc,
                                             "masking": (d.get("dataMaskingPolicy") or {}).get("predefinedExpression")
                                             or ("routine" if (d.get("dataMaskingPolicy") or {}).get("routine") else None)})
            ctx.coverage.ok(MODULE, "data_masking", len(out["data_policies"]))
        except ApiError as exc:
            ctx.coverage.fail(MODULE, "data_masking", exc, scope=f"{pid}/{loc}")
    try:
        for p in ctx.gcp.paginate(f"{DLP}/projects/{pid}/locations/{loc}/tableDataProfiles", "tableDataProfiles",
                                  {"pageSize": 100}, max_items=5000):
            out["dlp_table_profiles"].append({
                "table": (p.get("fullResource") or "").split("/projects/")[-1].replace("/datasets/", ".").replace("/tables/", "."),
                "sensitivity": (p.get("sensitivityScore") or {}).get("score"),
                "risk": (p.get("dataRiskLevel") or {}).get("score"),
                "info_types": sorted({(i.get("infoType") or {}).get("name") for i in p.get("predictedInfoTypes") or []})})
        ctx.coverage.ok(MODULE, "dlp_profiles", 1)
    except ApiError as exc:
        if exc.status not in (400, 404):
            ctx.coverage.fail(MODULE, "dlp_profiles", exc, scope=f"{pid}/{loc}")
    return out


def _governance(ctx: Context, pid: str) -> dict:
    locs = sorted({loc.lower() for loc in ctx.regions_for(pid)})
    targets = [("global", False)] + [(loc, True) for loc in locs]
    parts = ctx.run_parallel(lambda x: _governance_loc(ctx, pid, x[0], x[1]), targets, f"governance/{pid}", workers=ctx.io_workers)
    out = {"taxonomies": [], "data_policies": [], "dlp_table_profiles": []}
    for part in parts:
        for k in out:
            out[k].extend(part[k])
    for k in out:
        out[k].sort(key=lambda r: json.dumps(r, sort_keys=True, default=str))
    return out


def _vpc_sc(ctx: Context) -> list[dict]:
    orgs = ctx.load_raw("discovery", "organizations") or []
    org_ids = [o["name"].split("/")[-1] for o in orgs if o.get("name")]
    if ctx.config["gcp"].get("organization_id"):
        org_ids = sorted(set(org_ids) | {str(ctx.config["gcp"]["organization_id"])})
    out = []
    for org in org_ids:
        try:
            for pol in ctx.gcp.paginate(f"{ACM}/accessPolicies", "accessPolicies", {"parent": f"organizations/{org}"}):
                for per in ctx.gcp.paginate(f"{ACM}/{pol['name']}/servicePerimeters", "servicePerimeters"):
                    st = per.get("status") or {}
                    out.append({"policy": pol.get("title"), "perimeter": per.get("title"), "type": per.get("perimeterType"),
                                "projects": [r.split("/")[-1] for r in st.get("resources") or []],
                                "restricted_services": st.get("restrictedServices") or [],
                                "dry_run": bool(per.get("useExplicitDryRunSpec"))})
            ctx.coverage.ok(MODULE, "vpc_service_controls", len(out))
        except ApiError as exc:
            ctx.coverage.fail(MODULE, "vpc_service_controls", exc, scope=f"organizations/{org}")
    if not org_ids:
        ctx.coverage.limitation(MODULE, "vpc_service_controls", "Organização não visível; VPC-SC não avaliado")
    return out


def collect(ctx: Context) -> dict:
    cap = int(ctx.config["security"].get("max_datasets_acl", 500))
    result: dict = {"projects": {}}

    def per(pid):
        checks = {"iam": lambda: _iam(ctx, pid), "sa_keys": lambda: _sa_keys(ctx, pid), "datasets": lambda: _datasets(ctx, pid, cap),
                  "org_policies": lambda: _org_policies(ctx, pid), "governance": lambda: _governance(ctx, pid)}
        out: dict = {}

        def one(item):
            out[item[0]] = item[1]()

        ctx.run_parallel(one, list(checks.items()), f"security/{pid}", workers=len(checks))
        defaults = {"iam": {}, "sa_keys": [], "datasets": [], "org_policies": {}, "governance": {}}
        result["projects"][pid] = {k: out.get(k, defaults[k]) for k in checks}

    ctx.run_parallel(per, ctx.project_ids(), "security")
    result["vpc_service_controls"] = _vpc_sc(ctx)
    ctx.save_raw(MODULE, "security", result)
    for comp, src in [("iam_policies", "CRM v3 getIamPolicy"), ("service_account_keys", "IAM serviceAccounts.keys.list"),
                      ("dataset_acl", "BigQuery datasets.get"), ("org_policies", "Org Policy v2 getEffectivePolicy"),
                      ("policy_tags", "Data Catalog taxonomies"), ("data_masking", "BigQuery Data Policy API"),
                      ("dlp_profiles", "Sensitive Data Protection tableDataProfiles"),
                      ("vpc_service_controls", "Access Context Manager")]:
        ctx.coverage.finalize(MODULE, comp, source=src)
    return {"projects": len(result["projects"])}
