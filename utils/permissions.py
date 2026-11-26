"""Matriz de permissões mínimas por módulo + preflight (item 21).

O preflight NUNCA concede permissões nem habilita APIs: apenas testa
(testIamPermissions, Service Usage, dry run) e imprime os comandos para que um
administrador decida e execute.
"""
from __future__ import annotations

from collections import defaultdict

from utils.bq import info_schema
from utils.http import ApiError
from utils.logging import get_logger

log = get_logger("permissions")
CRM = "https://cloudresourcemanager.googleapis.com/v3"
SU = "https://serviceusage.googleapis.com/v1"

MATRIX: dict[str, dict] = {
    "discovery": {
        "apis": ["cloudresourcemanager.googleapis.com", "serviceusage.googleapis.com", "cloudasset.googleapis.com",
                 "cloudbilling.googleapis.com", "iam.googleapis.com", "compute.googleapis.com", "monitoring.googleapis.com"],
        "roles": ["roles/browser", "roles/cloudasset.viewer", "roles/serviceusage.serviceUsageViewer", "roles/iam.securityReviewer",
                  "roles/compute.networkViewer", "roles/monitoring.viewer", "roles/billing.viewer (billing account)"],
        "permissions": ["resourcemanager.projects.get", "serviceusage.services.list", "cloudasset.assets.searchAllResources",
                        "iam.serviceAccounts.list", "compute.networks.list", "compute.subnetworks.list", "compute.routers.list",
                        "compute.vpnTunnels.list", "compute.interconnectAttachments.list", "monitoring.timeSeries.list"],
        "non_project": ["resourcemanager.folders.list / organizations.get (organização)", "billing.accounts.list (billing account)"],
    },
    "bigquery": {
        "apis": ["bigquery.googleapis.com", "bigquerydatatransfer.googleapis.com", "bigqueryreservation.googleapis.com",
                 "bigqueryconnection.googleapis.com", "monitoring.googleapis.com"],
        "roles": ["roles/bigquery.metadataViewer", "roles/bigquery.resourceViewer", "roles/bigquery.jobUser (projeto de execução)"],
        "permissions": ["bigquery.datasets.get", "bigquery.tables.list", "bigquery.tables.get", "bigquery.routines.list",
                        "bigquery.routines.get", "bigquery.jobs.create", "bigquery.jobs.listAll", "bigquery.transfers.get",
                        "bigquery.reservations.list", "bigquery.bireservations.get", "bigquery.capacityCommitments.list", "bigquery.reservationAssignments.list",
                        "bigquery.rowAccessPolicies.list", "bigquery.connections.list"],
        "non_project": [],
    },
    "storage": {
        "apis": ["storage.googleapis.com", "monitoring.googleapis.com"],
        "roles": ["papel customizado (storage.buckets.list/get/getIamPolicy + storage.objects.list)", "roles/monitoring.viewer"],
        "permissions": ["storage.buckets.list", "storage.buckets.get", "storage.buckets.getIamPolicy", "storage.objects.list"],
        "non_project": [],
        "note": "Evite roles/storage.objectViewer: inclui storage.objects.get (leitura de conteúdo). A ferramenta só lista nomes/tamanhos.",
    },
    "pipelines": {
        "apis": ["composer.googleapis.com", "dataflow.googleapis.com", "dataproc.googleapis.com", "dataform.googleapis.com",
                 "datafusion.googleapis.com", "pubsub.googleapis.com", "datastream.googleapis.com", "cloudscheduler.googleapis.com",
                 "workflows.googleapis.com", "workflowexecutions.googleapis.com", "cloudfunctions.googleapis.com", "run.googleapis.com"],
        "roles": ["roles/composer.user (+ papel Viewer no Airflow)", "roles/dataflow.viewer", "roles/dataproc.viewer", "roles/dataform.viewer",
                  "roles/datafusion.viewer", "roles/pubsub.viewer", "roles/datastream.viewer", "roles/cloudscheduler.viewer",
                  "roles/workflows.viewer", "roles/cloudfunctions.viewer", "roles/run.viewer"],
        "permissions": ["composer.environments.list", "composer.environments.get", "dataflow.jobs.list", "dataflow.jobs.get",
                        "dataproc.clusters.list", "dataproc.jobs.list", "dataproc.batches.list", "dataform.repositories.list",
                        "dataform.workflowInvocations.list", "dataform.workflowConfigs.list", "dataform.compilationResults.get", "dataform.compilationResults.query",
                        "datafusion.instances.list", "pubsub.topics.list", "pubsub.subscriptions.list", "datastream.streams.list",
                        "datastream.connectionProfiles.list", "cloudscheduler.jobs.list", "workflows.workflows.list",
                        "workflows.executions.list", "cloudfunctions.functions.list", "run.services.list", "run.jobs.list"],
        "non_project": [],
    },
    "services": {
        "apis": ["sqladmin.googleapis.com", "alloydb.googleapis.com", "spanner.googleapis.com", "compute.googleapis.com",
                 "container.googleapis.com", "aiplatform.googleapis.com", "notebooks.googleapis.com", "dataplex.googleapis.com",
                 "looker.googleapis.com"],
        "roles": ["roles/cloudsql.viewer", "roles/alloydb.viewer", "roles/spanner.viewer", "roles/compute.viewer",
                  "roles/container.clusterViewer", "roles/aiplatform.viewer", "roles/notebooks.viewer", "roles/dataplex.viewer",
                  "roles/looker.viewer"],
        "permissions": ["cloudsql.instances.list", "alloydb.clusters.list", "spanner.instances.list", "compute.instances.list",
                        "container.clusters.list", "aiplatform.endpoints.list", "aiplatform.models.list", "aiplatform.pipelineJobs.list",
                        "notebooks.instances.list", "dataplex.lakes.list", "dataplex.datascans.list", "looker.instances.list"],
        "non_project": [],
    },
    "billing": {
        "apis": ["bigquery.googleapis.com", "cloudbilling.googleapis.com"],
        "roles": ["roles/bigquery.dataViewer SOMENTE no dataset do Billing Export", "roles/bigquery.jobUser", "roles/billing.viewer"],
        "permissions": ["bigquery.jobs.create"],
        "non_project": ["bigquery.tables.getData no dataset do Billing Export"],
    },
    "security": {
        "apis": ["cloudresourcemanager.googleapis.com", "iam.googleapis.com", "orgpolicy.googleapis.com", "accesscontextmanager.googleapis.com",
                 "datacatalog.googleapis.com", "bigquerydatapolicy.googleapis.com", "dlp.googleapis.com"],
        "roles": ["roles/iam.securityReviewer", "roles/orgpolicy.policyViewer", "roles/accesscontextmanager.policyReader (organização)",
                  "roles/datacatalog.viewer", "roles/bigquerydatapolicy.viewer", "roles/dlp.dataProfilesReader"],
        "permissions": ["resourcemanager.projects.getIamPolicy", "iam.serviceAccountKeys.list", "orgpolicy.policy.get",
                        "datacatalog.taxonomies.list", "bigquery.dataPolicies.list"],
        "non_project": ["accesscontextmanager.policies.list / servicePerimeters.list (organização)"],
    },
    "lineage": {
        "apis": ["datalineage.googleapis.com"],
        "roles": ["roles/datalineage.viewer"],
        "permissions": ["datalineage.locations.searchLinks"],
        "non_project": [],
    },
}

POWERBI_MATRIX = [
    {"item": "Registro de aplicativo (service principal) no Microsoft Entra ID", "detail": "Sem permissões de API Power BI com admin consent (exigência da Admin API para SP)"},
    {"item": "Grupo de segurança contendo o SP", "detail": "Usado nas configurações de tenant abaixo"},
    {"item": "Tenant setting: Service principals can use read-only admin APIs", "detail": "Habilitar para o grupo do SP (Admin portal > Admin API settings)"},
    {"item": "Tenant setting: Enhance admin APIs responses with detailed metadata", "detail": "Necessário para datasetSchema (tabelas/colunas/medidas)"},
    {"item": "Tenant setting: Enhance admin APIs responses with DAX and mashup expressions", "detail": "Necessário para datasetExpressions (DAX/M)"},
    {"item": "Alternativa delegada", "detail": "Usuário Fabric Administrator com escopo Tenant.Read.All (device code)"},
    {"item": "Histórico de refresh por dataset", "detail": "SP como membro (Viewer ou superior) dos workspaces avaliados; ou usar admin/capacities/refreshables (capacidades dedicadas)"},
    {"item": "Activity log", "detail": "admin/activityevents (mesma permissão de read-only admin APIs); 1 dia UTC por chamada, ~30 dias de retenção"},
]


def matrix_markdown(principal: str = "serviceAccount:SA_EMAIL", org_id: str = "ORG_ID", project: str = "PROJECT_ID") -> str:
    lines = ["# Matriz de permissões mínimas", "",
             "A ferramenta é somente leitura. Nenhuma permissão é concedida e nenhuma API é habilitada automaticamente.", "",
             "| Módulo | APIs | Papéis predefinidos (alternativa) | Permissões testadas no projeto | Fora do projeto |",
             "|---|---|---|---|---|"]
    for mod, m in MATRIX.items():
        lines.append(f"| {mod} | {', '.join(m['apis'])} | {'<br>'.join(m['roles'])} | {', '.join(m['permissions'])} | "
                     f"{'<br>'.join(m.get('non_project') or []) or '—'} |")
    lines += ["", "## Papel customizado recomendado (menor privilégio)", "",
              "Escopo de **projeto** (padrão; é o que o script `scripts/setup_assessment_sa.sh` cria). Para escopo de organização use "
              "`python3 main.py --print-custom-role organization`.", "", "```yaml", custom_role_yaml("project"), "```", "",
              "## Comandos para o administrador (revisar antes de executar)", "",
              "Forma recomendada: o script cria a service account, o papel customizado e os bindings, mostra cada comando e pede confirmação.", "",
              "```bash",
              f"./scripts/setup_assessment_sa.sh --project {project} --executor user:PESSOA@EMPRESA.COM          # escopo de projeto",
              f"./scripts/setup_assessment_sa.sh --project {project} --org {org_id} --executor group:TIME@EMPRESA.COM  # escopo de organização",
              "```", "",
              "Equivalente manual (escopo de organização). Em projetos com bindings condicionais, use sempre `--condition=None`:", "",
              "```bash",
              f"gcloud iam roles create gcpDataAssessmentReader --organization={org_id} --file=config/iam/custom_role_organization.yaml",
              f"gcloud organizations add-iam-policy-binding {org_id} --member={principal} \\",
              f"  --role=organizations/{org_id}/roles/gcpDataAssessmentReader --condition=None",
              "# Projeto onde os jobs de metadados (INFORMATION_SCHEMA) serão executados:",
              f"gcloud projects add-iam-policy-binding {project} --member={principal} --role=roles/bigquery.jobUser --condition=None",
              "# Billing Export (somente o dataset da exportação):",
              "bq add-iam-policy-binding --member=" + principal + " --role=roles/bigquery.dataViewer BILLING_PROJECT:BILLING_DATASET",
              "# Organização (VPC-SC / Org Policy / folders):",
              f"gcloud organizations add-iam-policy-binding {org_id} --member={principal} --role=roles/accesscontextmanager.policyReader --condition=None",
              f"gcloud organizations add-iam-policy-binding {org_id} --member={principal} --role=roles/orgpolicy.policyViewer --condition=None",
              f"gcloud organizations add-iam-policy-binding {org_id} --member={principal} --role=roles/browser --condition=None",
              "```", "", "## Power BI / Microsoft Entra ID", "", "| Item | Detalhe |", "|---|---|"]
    lines += [f"| {p['item']} | {p['detail']} |" for p in POWERBI_MATRIX]
    return "\n".join(lines) + "\n"


# Permissões que só existem em papéis customizados criados na organização (não valem em papel de projeto).
ORG_ONLY_PERMISSIONS = {"resourcemanager.folders.list", "resourcemanager.organizations.get", "resourcemanager.projects.list",
                        "accesscontextmanager.policies.list", "accesscontextmanager.servicePerimeters.list", "billing.accounts.list",
                        "billing.accounts.get", "billing.resourceAssociations.list"}


def custom_role_permissions(scope: str = "project") -> list[str]:
    """Permissões do papel customizado. scope='project' (validado no GCP real) ou 'organization' (inclui permissões de org/billing)."""
    perms = {p for m in MATRIX.values() for p in m["permissions"]} | {"cloudasset.assets.searchAllIamPolicies"}
    if scope == "organization":
        perms |= ORG_ONLY_PERMISSIONS
    return sorted(perms)


def custom_role_yaml(scope: str = "project") -> str:
    perms = custom_role_permissions(scope)
    return ("title: GCP Data Platform Assessment Reader\n"
            "description: Leitura de metadados para assessment (sem acesso a dados de negocio)\n"
            "stage: GA\nincludedPermissions:\n" + "\n".join(f"- {p}" for p in perms))


def _test_perms(api, project: str, perms: list[str]) -> tuple[set, set]:
    """Retorna (concedidas, não testáveis). Divide o lote se alguma permissão for inválida no recurso."""
    if not perms:
        return set(), set()
    try:
        res = api.post(f"{CRM}/projects/{project}:testIamPermissions", json_body={"permissions": perms})
        return set(res.get("permissions") or []), set()
    except ApiError as exc:
        if exc.status == 400:
            if len(perms) == 1:
                return set(), set(perms)
            mid = len(perms) // 2
            g1, u1 = _test_perms(api, project, perms[:mid])
            g2, u2 = _test_perms(api, project, perms[mid:])
            return g1 | g2, u1 | u2
        raise


WORKLOAD_QUERIES_PER_REGION = 13  # consultas sobre JOBS/JOBS_TIMELINE emitidas por região (ver docs/SQL_QUERIES.md)


def _estimate_jobs_cost(ctx, pid: str, loc: str) -> dict:
    """Dry run de uma consulta representativa sobre JOBS para estimar o custo da coleta de workloads."""
    days = min(int(ctx.days), 180)
    sql = (f"SELECT TIMESTAMP_TRUNC(creation_time, HOUR) h, COUNT(1) n, SUM(total_slot_ms) s, ANY_VALUE(query) q "
           f"FROM {info_schema(pid, loc, 'JOBS_BY_PROJECT')} "
           f"WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY) GROUP BY 1")
    price = float(ctx.config.get("pricing_estimates", {}).get("bq_on_demand_per_tib", 6.25))
    try:
        est, _ = ctx.bq.dry_run(pid, loc, sql)
    except ApiError as exc:
        return {"location": loc, "error": exc.message[:200]}
    total = est * WORKLOAD_QUERIES_PER_REGION
    cap = int(ctx.config["bigquery"]["max_bytes_billed_per_query"])
    return {"location": loc, "window_days": days, "bytes_per_query": est, "queries": WORKLOAD_QUERIES_PER_REGION,
            "bytes_total": total, "estimated_cost": round(total / 1024 ** 4 * price, 2),
            "currency": ctx.config.get("pricing_estimates", {}).get("currency", "USD"),
            "exceeds_per_query_cap": est > cap}


def preflight(ctx, modules: list[str], max_projects: int = 25) -> dict:
    """Valida autenticação, APIs, permissões e acesso às visões INFORMATION_SCHEMA."""
    from collectors.discovery import resolve_projects

    report: dict = {"identity": None, "projects": [], "modules": modules, "powerbi": None, "missing": [], "commands": []}
    if ctx.gcp is not None:
        report["identity"] = {"principal_hash": ctx.identity.principal_hash, "method": ctx.identity.method,
                              "quota_project": ctx.identity.quota_project}
        projects = resolve_projects(ctx, with_bq_locations=True)
        selected = [p for p in projects if p.get("selected")][:max_projects]
        wanted_perms = sorted({p for m in modules if m in MATRIX for p in MATRIX[m]["permissions"]})
        wanted_apis = sorted({a for m in modules if m in MATRIX for a in MATRIX[m]["apis"]})
        for p in selected:
            pid = p["project_id"]
            rec = {"project_id": pid, "environment": p.get("environment"), "apis_enabled": [], "apis_missing": [],
                   "permissions_granted": [], "permissions_missing": [], "permissions_untestable": [], "bigquery_views": {}}
            try:
                enabled = {s.get("config", {}).get("name") for s in ctx.gcp.paginate(
                    f"{SU}/projects/{pid}/services", "services", {"filter": "state:ENABLED", "pageSize": 200})}
                rec["apis_enabled"] = sorted(a for a in wanted_apis if a in enabled)
                rec["apis_missing"] = sorted(a for a in wanted_apis if a not in enabled)
            except ApiError as exc:
                rec["apis_error"] = exc.kind
            try:
                granted, untestable = set(), set()
                for i in range(0, len(wanted_perms), 90):
                    g, u = _test_perms(ctx.gcp, pid, wanted_perms[i:i + 90])
                    granted |= g
                    untestable |= u
                rec["permissions_granted"] = sorted(granted)
                rec["permissions_untestable"] = sorted(untestable)
                rec["permissions_missing"] = sorted(set(wanted_perms) - granted - untestable)
            except ApiError as exc:
                rec["permissions_error"] = exc.kind
            if "bigquery" in modules and ctx.bq is not None:
                rec["bigquery_view_errors"] = {}
                rec["cost_estimate"] = []

                def probe(loc, pid=pid):
                    views, errors, est = {}, {}, None
                    for view in ("TABLES", "TABLE_STORAGE", "JOBS_BY_PROJECT", "JOBS_TIMELINE_BY_PROJECT"):
                        ref = info_schema(pid, loc, view)
                        ok = ctx.bq.view_schema(pid, loc, ref) is not None
                        views[f"{loc}:{view}"] = ok
                        if not ok:
                            errors[f"{loc}:{view}"] = ctx.bq.schema_errors.get((pid, loc, ref), "")
                    if views.get(f"{loc}:JOBS_BY_PROJECT"):
                        est = _estimate_jobs_cost(ctx, pid, loc)
                    return loc, views, errors, est

                # uma localização por tarefa: dry runs independentes (o teto de requisições é o semáforo global)
                for loc, views, errors, est in sorted(ctx.run_parallel(probe, p.get("bigquery_locations") or [], f"preflight/{pid}",
                                                                      workers=ctx.io_workers), key=lambda r: r[0]):
                    rec["bigquery_views"].update(views)
                    rec["bigquery_view_errors"].update(errors)
                    if est:
                        rec["cost_estimate"].append(est)
            report["projects"].append(rec)
            for perm in rec["permissions_missing"]:
                mods = [m for m in modules if perm in MATRIX.get(m, {}).get("permissions", [])]
                report["missing"].append({"project_id": pid, "type": "permission", "permission": perm, "modules": mods})
            for api in rec["apis_missing"]:
                report["missing"].append({"project_id": pid, "type": "api_not_enabled", "api": api,
                                          "note": "Se o serviço não é usado no projeto, a API desabilitada é esperada (não é erro)."})
        missing_by_project = defaultdict(list)
        for m in report["missing"]:
            if m["type"] == "permission":
                missing_by_project[m["project_id"]].append(m["permission"])
        for pid, perms in missing_by_project.items():
            report["commands"].append(f"# {pid}: faltam {len(perms)} permissões -> conceder o papel customizado (ver permissions_matrix.md)")
            report["commands"].append(f"./scripts/setup_assessment_sa.sh --project {pid} --executor user:PESSOA@EMPRESA.COM   "
                                      f"# cria/atualiza o papel de menor privilégio e os acessos (use --dry-run para revisar)")
    if ctx.powerbi is not None:
        pbi = {"token": "ok"}
        for name, url in [("admin_groups", "https://api.powerbi.com/v1.0/myorg/admin/groups?$top=1"),
                          ("admin_capacities", "https://api.powerbi.com/v1.0/myorg/admin/capacities")]:
            try:
                ctx.powerbi.get(url)
                pbi[name] = "ok"
            except ApiError as exc:
                pbi[name] = f"{exc.status} {exc.reason or exc.kind}"
                report["missing"].append({"type": "powerbi", "endpoint": name, "error": pbi[name]})
        report["powerbi"] = pbi
    return report


def preflight_markdown(rep: dict) -> str:
    lines = ["# Preflight — validação de autenticação, APIs e permissões", ""]
    if rep.get("identity"):
        i = rep["identity"]
        lines += [f"- Autenticação GCP: **{i['method']}** (principal hash `{i['principal_hash']}`), quota project `{i['quota_project']}`", ""]
    else:
        lines += ["- Autenticação GCP: não realizada", ""]
    if rep["projects"]:
        lines += ["| Projeto | Ambiente | APIs ausentes | Permissões ausentes | Não testáveis | Visões BigQuery acessíveis |", "|---|---|---|---|---|---|"]
        for p in rep["projects"]:
            views = p.get("bigquery_views") or {}
            ok = sum(1 for v in views.values() if v)
            lines.append(f"| {p['project_id']} | {p.get('environment')} | {len(p['apis_missing'])} | {len(p['permissions_missing'])} | "
                         f"{len(p['permissions_untestable'])} | {ok}/{len(views)} |")
        lines.append("")
        for p in rep["projects"]:
            if p["permissions_missing"] or p["apis_missing"]:
                lines += [f"### {p['project_id']}", ""]
                if p["permissions_missing"]:
                    lines.append("Permissões ausentes: " + ", ".join(f"`{x}`" for x in p["permissions_missing"]))
                if p["apis_missing"]:
                    lines.append("APIs não habilitadas (esperado se o serviço não é usado): " + ", ".join(f"`{x}`" for x in p["apis_missing"]))
                bad = [k for k, v in (p.get("bigquery_views") or {}).items() if not v]
                if bad:
                    lines.append("Visões INFORMATION_SCHEMA inacessíveis:")
                    for k in bad:
                        lines.append(f"- `{k}` — {(p.get('bigquery_view_errors') or {}).get(k, '')[:220]}")
                lines.append("")
    est = [dict(e, project_id=p["project_id"]) for p in rep["projects"] for e in p.get("cost_estimate") or [] if "bytes_total" in e]
    if est:
        lines += ["## Estimativa de custo da coleta de workloads (dry run, preço on-demand configurado)", "",
                  "| Projeto | Região | Janela | Bytes/consulta | Consultas | Total estimado | Custo estimado | Excede limite por consulta |",
                  "|---|---|---|---|---|---|---|---|"]
        for e in est:
            lines.append(f"| {e['project_id']} | {e['location']} | {e['window_days']} d | {e['bytes_per_query'] / 1e9:,.1f} GB | {e['queries']} | "
                         f"{e['bytes_total'] / 1e9:,.1f} GB | {e['currency']} {e['estimated_cost']:,.2f} | {'**sim**' if e['exceeds_per_query_cap'] else 'não'} |")
        lines += ["", "_Projetos com reservas (Editions) consomem slots em vez de bytes on-demand. Reduza `--days` ou ajuste "
                  "`bigquery.max_bytes_billed_per_query` / `max_total_bytes_billed` conforme a política do cliente._", ""]
    if rep.get("powerbi"):
        lines += ["## Power BI", ""] + [f"- {k}: {v}" for k, v in rep["powerbi"].items()] + [""]
    if rep.get("commands"):
        lines += ["## Comandos sugeridos (não executados pela ferramenta)", "", "```bash", *rep["commands"], "```", ""]
    lines.append("Consulte `permissions_matrix.md` para a matriz completa e o papel customizado.")
    return "\n".join(lines) + "\n"
