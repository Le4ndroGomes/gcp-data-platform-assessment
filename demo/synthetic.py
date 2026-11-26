"""Massa de dados sintética para demonstração offline (`python main.py --demo`).

Empresa fictícia "Rede Exemplo Saúde". Todos os nomes, números e identificadores
são inventados e determinísticos (seed fixa). Os arquivos seguem exatamente o
formato produzido pelos collectors; o Power BI é gerado como artefatos PBIP/TMDL e
JSON de scanner e então processado pelos parsers reais do modo offline.
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collectors import powerbi as pbi_mod
from collectors.pipelines import _record
from core.context import Context

R = random.Random(42)
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
ORG = "organizations/112233445566"
PROJECTS = [
    ("rx-dados-prd", "prod", ["southamerica-east1", "US"]), ("rx-ingestao-prd", "prod", ["southamerica-east1"]),
    ("rx-bi-prd", "prod", ["southamerica-east1"]), ("rx-dados-hml", "hml", ["southamerica-east1"]),
    ("rx-ml-dev", "dev", ["US"]), ("rx-sandbox-analytics", "dev", ["southamerica-east1"]),
]
DATASETS = {
    "rx-dados-prd": [("raw_prontuario", 40, 9.0), ("raw_faturamento", 30, 8.5), ("stg_assistencial", 35, 7.5),
                     ("dw_assistencial", 45, 8.0), ("dw_faturamento", 38, 8.2), ("mart_bi", 30, 6.5), ("finops", 1, 7.0)],
    "rx-ingestao-prd": [("landing_oracle", 60, 8.0), ("landing_sqlserver", 25, 7.0), ("eventos_app", 6, 9.5)],
    "rx-bi-prd": [("semantic_bi", 25, 6.0), ("extracoes_bi", 15, 5.5)],
    "rx-dados-hml": [("dw_assistencial", 30, 6.0), ("dw_faturamento", 25, 6.0)],
    "rx-ml-dev": [("ml_features", 12, 7.5), ("ml_predicoes", 6, 6.0)],
    "rx-sandbox-analytics": [("sandbox_analistas", 40, 5.0)],
}
SENSITIVE = ["cpf_paciente", "nome_paciente", "dt_nascimento", "cid_principal", "nr_prontuario", "telefone_contato", "email_paciente",
             "sexo", "cns", "ds_diagnostico", "nome_mae"]
FORCED = {"dw_assistencial": ["assistencial_fato_atendimento"], "stg_assistencial": ["assistencial_atendimento"],
          "dw_faturamento": ["faturamento_faturamento", "faturamento_unidade"], "mart_bi": ["bi_painel"],
          "semantic_bi": ["bi_fato_receita"], "landing_oracle": ["oracle_atendimento", "oracle_internacao"], "eventos_app": ["app_evento"]}
GENERIC = ["id_atendimento", "dt_atendimento", "id_unidade", "vl_total", "qt_itens", "cd_procedimento", "id_convenio", "dt_carga",
           "status", "id_medico", "cd_setor", "vl_glosa", "dt_alta", "tp_atendimento"]


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _save(ctx: Context, module: str, name: str, data) -> None:
    ctx.save_raw(module, name, data)


def generate(ctx: Context) -> None:
    red = ctx.redactor
    cov = ctx.coverage
    ctx.config["execution"]["timezone"] = ctx.config["execution"].get("timezone") or "America/Sao_Paulo"
    ctx.config["billing"]["export_table"] = None
    days = min(ctx.days, 180)

    # ---------------------------------------------------------------- discovery
    projects = []
    for i, (pid, env, locs) in enumerate(PROJECTS):
        projects.append({"project_id": pid, "project_number": str(800000000000 + i), "display_name": pid, "state": "ACTIVE",
                         "parent": "folders/90001" if env == "prod" else "folders/90002", "labels": {"env": env, "centro_custo": "dados"},
                         "create_time": "2023-03-01T10:00:00Z", "selected": True, "environment": env,
                         "bigquery_locations": locs, "bigquery_dataset_count": len(DATASETS.get(pid, []))})
    projects.append({"project_id": "sys-legacy-scripts-01", "project_number": "800000000099", "display_name": "apps script",
                     "state": "ACTIVE", "parent": "folders/90002", "labels": {}, "selected": False, "environment": "unknown",
                     "bigquery_locations": []})
    ctx.projects = projects
    _save(ctx, "discovery", "projects", projects)
    _save(ctx, "discovery", "organizations", [{"name": ORG, "display_name": "redeexemplo.com.br", "state": "ACTIVE"}])
    _save(ctx, "discovery", "folders", [{"name": "folders/90001", "display_name": "Produção", "parent": ORG, "state": "ACTIVE"},
                                        {"name": "folders/90002", "display_name": "Não produtivo", "parent": ORG, "state": "ACTIVE"}])
    base_apis = ["bigquery.googleapis.com", "storage.googleapis.com", "monitoring.googleapis.com", "logging.googleapis.com",
                 "iam.googleapis.com", "cloudresourcemanager.googleapis.com", "compute.googleapis.com", "bigquerydatatransfer.googleapis.com",
                 "bigqueryreservation.googleapis.com", "bigqueryconnection.googleapis.com", "datacatalog.googleapis.com",
                 "secretmanager.googleapis.com", "cloudkms.googleapis.com", "artifactregistry.googleapis.com"]
    extra = {"rx-dados-prd": ["composer.googleapis.com", "dataform.googleapis.com", "dataflow.googleapis.com", "cloudscheduler.googleapis.com",
                              "workflows.googleapis.com", "workflowexecutions.googleapis.com", "dataplex.googleapis.com", "datalineage.googleapis.com"],
             "rx-ingestao-prd": ["datastream.googleapis.com", "pubsub.googleapis.com", "dataflow.googleapis.com", "cloudfunctions.googleapis.com",
                                 "run.googleapis.com", "sqladmin.googleapis.com", "cloudscheduler.googleapis.com", "dataproc.googleapis.com"],
             "rx-bi-prd": ["sqladmin.googleapis.com"], "rx-ml-dev": ["aiplatform.googleapis.com", "notebooks.googleapis.com"],
             "rx-dados-hml": ["dataform.googleapis.com"], "rx-sandbox-analytics": []}
    enabled = {p: sorted(base_apis + extra.get(p, [])) for p, _, _ in PROJECTS}
    _save(ctx, "discovery", "services_enabled", enabled)
    assets = {p: [] for p, _, _ in PROJECTS}

    def asset(pid, typ, svc, name, loc, state="ACTIVE"):
        assets[pid].append({"name": f"//{typ.split('/')[0]}/projects/{pid}/{name}", "asset_type": typ, "location": loc, "state": state,
                            "labels": {}, "create_time": "2024-01-10T00:00:00Z", "update_time": "2026-10-01T00:00:00Z",
                            "display_name": name.split("/")[-1], "service": svc})

    asset("rx-dados-prd", "composer.googleapis.com/Environment", "composer", "environments/composer-dados-prd", "southamerica-east1", "RUNNING")
    asset("rx-dados-prd", "dataform.googleapis.com/Repository", "dataform", "repositories/dw-saude", "southamerica-east1")
    for j in range(5):
        asset("rx-dados-prd", "cloudscheduler.googleapis.com/Job", "scheduler", f"jobs/sch-{j}", "southamerica-east1", "ENABLED")
    asset("rx-dados-prd", "workflows.googleapis.com/Workflow", "workflows", "workflows/wf-fechamento-mensal", "southamerica-east1")
    asset("rx-dados-prd", "dataplex.googleapis.com/Lake", "dataplex", "lakes/lake-saude", "southamerica-east1")
    asset("rx-ingestao-prd", "datastream.googleapis.com/Stream", "datastream", "streams/cdc-oracle-tasy", "southamerica-east1", "RUNNING")
    asset("rx-ingestao-prd", "datastream.googleapis.com/Stream", "datastream", "streams/cdc-sqlserver-lab", "southamerica-east1", "RUNNING")
    for t in ("eventos-app-paciente", "resultados-exames", "agenda-consultas"):
        asset("rx-ingestao-prd", "pubsub.googleapis.com/Topic", "pubsub", f"topics/{t}", "global")
    asset("rx-ingestao-prd", "sqladmin.googleapis.com/Instance", "cloudsql", "instances/mysql-portal-paciente", "southamerica-east1", "RUNNABLE")
    asset("rx-bi-prd", "sqladmin.googleapis.com/Instance", "cloudsql", "instances/pg-metadados-bi", "southamerica-east1", "RUNNABLE")
    asset("rx-ingestao-prd", "dataproc.googleapis.com/Cluster", "dataproc", "clusters/dp-legado-hive", "southamerica-east1", "RUNNING")
    for k in range(6):
        asset("rx-bi-prd" if k < 2 else "rx-ingestao-prd", "compute.googleapis.com/Instance", "compute", f"instances/vm-{k}",
              "southamerica-east1-a", "RUNNING")
    asset("rx-ml-dev", "aiplatform.googleapis.com/Endpoint", "vertex", "endpoints/risco-readmissao", "us-central1")
    asset("rx-ml-dev", "aiplatform.googleapis.com/Model", "vertex", "models/readmissao-xgb", "us-central1")
    _save(ctx, "discovery", "assets", assets)
    _save(ctx, "discovery", "billing_info", {p: {"billing_account": "billingAccounts/01A2B3-C4D5E6-F7G8H9", "billing_enabled": True} for p, _, _ in PROJECTS})
    _save(ctx, "discovery", "billing_accounts", [{"name": "billingAccounts/01A2B3-C4D5E6-F7G8H9", "display_name": "Rede Exemplo", "open": True}])
    sas = {p: [{"email": f"sa-{n}@{p}.iam.gserviceaccount.com", "display_name": n, "disabled": False, "unique_id": str(R.randint(10**17, 10**18))}
               for n in ("etl", "dataform", "composer", "powerbi-gateway", "app")] for p, _, _ in PROJECTS}
    _save(ctx, "discovery", "service_accounts", sas)
    nets = {p: {"compute_api_enabled": True, "networks": [{"name": "vpc-dados", "auto_create_subnetworks": False, "subnet_count": 2,
                                                            "routing_mode": "REGIONAL",
                                                            "peerings": [{"name": "servicenetworking-googleapis-com", "network": "servicenetworking", "state": "ACTIVE"}]}],
                "subnetworks": [{"name": "snet-sp", "region": "southamerica-east1", "network": "vpc-dados", "private_google_access": True},
                                {"name": "snet-us", "region": "us-central1", "network": "vpc-dados", "private_google_access": p != "rx-ml-dev"}],
                "vpn_tunnels": [{"name": "vpn-datacenter-1", "region": "southamerica-east1", "status": "ESTABLISHED"}] if p == "rx-ingestao-prd" else [],
                "interconnect_attachments": [], "cloud_nat_count": 1, "shared_vpc_host": False, "private_services_access": True}
            for p, _, _ in PROJECTS}
    _save(ctx, "discovery", "networks", nets)
    usage = {p: {"window_days": days, "requests_by_api": {"bigquery.googleapis.com": R.randint(200000, 900000),
                                                           "storage.googleapis.com": R.randint(50000, 400000),
                                                           **({"datastream.googleapis.com": 12000, "pubsub.googleapis.com": 2_400_000} if p == "rx-ingestao-prd" else {}),
                                                           **({"composer.googleapis.com": 8000, "dataform.googleapis.com": 2400} if p == "rx-dados-prd" else {}),
                                                           **({"aiplatform.googleapis.com": 50000} if p == "rx-ml-dev" else {})}} for p, _, _ in PROJECTS}
    _save(ctx, "discovery", "api_usage", usage)
    for comp in ("projects", "organizations", "folders", "services_enabled", "assets", "billing_info", "billing_accounts", "service_accounts",
                 "network", "api_usage", "bigquery_locations"):
        cov.ok("discovery", comp, 5)
        cov.finalize("discovery", comp, source=f"demo: {comp}")

    # ---------------------------------------------------------------- bigquery
    all_tables = []
    for pid, _, locs in PROJECTS:
        per_loc = {loc: {"project_id": pid, "location": loc, "schemata": [], "schemata_options": [], "tables": [], "table_options": [],
                         "table_storage": [], "columns_agg": [], "column_types": [], "sensitive_name_columns": [], "policy_tag_columns": [],
                         "views": [], "materialized_views": [], "routines": [], "dataset_level": {"row_access_policies": [],
                                                                                                  "partitions_by_table": [], "partitions_daily": []}}
                   for loc in locs}
        ds_list = []
        for ds, ntab, logmean in DATASETS.get(pid, []):
            loc = "US" if ds.startswith("ml_") or (pid == "rx-dados-prd" and ds == "finops") else locs[0]
            if loc not in per_loc:
                loc = locs[0]
            m = per_loc[loc]
            m["schemata"].append({"schema_name": ds, "location": loc, "creation_time": "2023-04-01T00:00:00+00:00",
                                  "last_modified_time": "2026-09-01T00:00:00+00:00"})
            if ds.startswith("dw_") and pid.endswith("prd"):
                m["schemata_options"].append({"schema_name": ds, "option_name": "storage_billing_model", "option_value": '"PHYSICAL"'})
            ds_list.append({"datasetReference": {"datasetId": ds, "projectId": pid}, "location": loc})
            if ds == "finops":
                names = ["gcp_billing_export_resource_v1_01A2B3_C4D5E6_F7G8H9"]
            else:
                names = [f"{ds.split('_', 1)[-1]}_{w}" for w in R.sample(
                    ["atendimento", "internacao", "exame", "prescricao", "faturamento", "glosa", "paciente", "agenda", "estoque", "procedimento",
                     "convenio", "medico", "unidade", "leito", "cirurgia", "triagem", "alta", "evolucao", "laudo", "pagamento", "contrato", "item_conta",
                     "titulo", "ocupacao", "producao", "custo", "indicador", "meta", "evento", "sessao", "produto", "lote", "fornecedor", "nota",
                     "pedido", "repasse", "auditoria", "autorizacao", "guia", "beneficiario", "plano", "rede", "prestador", "kpi", "dim_tempo",
                     "dim_unidade", "fato_receita", "fato_custo", "fato_ocupacao", "fato_atendimento", "historico", "snapshot_diario",
                     "log", "integracao", "parametro", "depara", "agregado", "resumo", "painel", "feature"], ntab)]
            forced = FORCED.get(ds, [])
            names = forced + [x for x in names if x not in forced][:max(ntab - len(forced), 0)]
            for tname in names:
                tt = "BASE TABLE"
                if ds.startswith(("mart_", "semantic_")) and R.random() < 0.35:
                    tt = "VIEW"
                elif ds == "extracoes_bi" and R.random() < 0.3:
                    tt = "MATERIALIZED VIEW"
                elif ds.startswith("landing_") and R.random() < 0.08:
                    tt = "EXTERNAL"
                elif ds.startswith("dw_") and R.random() < 0.05:
                    tt = "SNAPSHOT"
                if tname in forced:
                    tt = "BASE TABLE"
                ref = f"{pid}.{ds}.{tname}"
                created = NOW - timedelta(days=R.randint(60, 1100))
                m["tables"].append({"table_schema": ds, "table_name": tname, "table_type": tt, "creation_time": _iso(created),
                                    "base_table_catalog": pid if tt == "SNAPSHOT" else None, "base_table_schema": ds if tt == "SNAPSHOT" else None,
                                    "base_table_name": names[0] if tt == "SNAPSHOT" else None, "snapshot_time_ms": None, "is_insertable_into": "YES"})
                if tt == "EXTERNAL":
                    m["table_options"].append({"table_schema": ds, "table_name": tname, "option_name": "uris",
                                               "option_value": f'["gs://rx-landing-arquivos-prd/{tname}/*.parquet"]'})
                    m["table_options"].append({"table_schema": ds, "table_name": tname, "option_name": "format", "option_value": '"PARQUET"'})
                if tt in ("BASE TABLE", "MATERIALIZED VIEW", "SNAPSHOT"):
                    logical = int(math.exp(R.gauss(logmean + (2.5 if ds == "finops" else 0), 1.6)) * 1e6)
                    if ds == "eventos_app" and tname == names[0]:
                        logical = int(38e12)
                    lt_share = R.uniform(0.1, 0.9) if not ds.startswith("raw_") else R.uniform(0.5, 0.95)
                    phys = int(logical / R.uniform(3.5, 9.0))
                    modified = NOW - timedelta(days=R.choice([0, 0, 0, 1, 2, 7, 30, 120, 400]))
                    m["table_storage"].append({"table_schema": ds, "table_name": tname, "table_type": tt, "creation_time": _iso(created),
                                               "storage_last_modified_time": _iso(modified), "deleted": False,
                                               "total_rows": int(logical / R.uniform(150, 900)),
                                               "total_partitions": R.choice([0, 0, 1, 365, 730, 1200]) if tt == "BASE TABLE" else 0,
                                               "total_logical_bytes": logical, "active_logical_bytes": int(logical * (1 - lt_share)),
                                               "long_term_logical_bytes": int(logical * lt_share), "current_physical_bytes": phys,
                                               "total_physical_bytes": int(phys * 1.12) if tt != "SNAPSHOT" else int(phys * 0.05),
                                               "active_physical_bytes": int(phys * (1 - lt_share)), "long_term_physical_bytes": int(phys * lt_share),
                                               "time_travel_physical_bytes": int(phys * 0.08), "fail_safe_physical_bytes": int(phys * 0.04)})
                ncols = R.randint(8, 120)
                part = R.random() < 0.55 and tt == "BASE TABLE"
                m["columns_agg"].append({"table_schema": ds, "table_name": tname, "column_count": ncols,
                                         "partition_columns": "dt_atendimento" if part else None,
                                         "clustering_columns": "id_unidade,id_convenio" if part and R.random() < 0.5 else None,
                                         "struct_columns": R.choice([0, 0, 1, 3]), "array_columns": R.choice([0, 0, 2]),
                                         "json_columns": 1 if ds == "eventos_app" else 0, "geography_columns": 0})
                if ds.startswith(("raw_prontuario", "dw_assistencial", "landing_oracle", "stg_assistencial")):
                    for col in R.sample(SENSITIVE, R.randint(2, 6)):
                        pat = {"cpf_paciente": "cpf", "nome_paciente": "nome", "dt_nascimento": "nascimento", "cid_principal": "cid",
                               "nr_prontuario": "prontuario", "telefone_contato": "telefone", "email_paciente": "email", "sexo": "sexo",
                               "cns": "cns", "ds_diagnostico": "diagnostico", "nome_mae": "nome"}[col]
                        m["sensitive_name_columns"].append({"table_schema": ds, "table_name": tname, "column_name": col, "matched_pattern": pat})
                        if ds == "dw_assistencial" and col in ("cpf_paciente", "nome_paciente") and pid == "rx-dados-prd":
                            m["policy_tag_columns"].append({"table_schema": ds, "table_name": tname, "column_name": col, "field_path": col,
                                                            "policy_tags": ["projects/rx-dados-prd/locations/southamerica-east1/taxonomies/1/policyTags/2"]})
                if tt in ("VIEW", "MATERIALIZED VIEW"):
                    feats = _view_features()
                    m["views"].append({"table_schema": ds, "table_name": tname, "use_standard_sql": "YES", **feats})
                    if tt == "MATERIALIZED VIEW":
                        m["materialized_views"].append({"table_schema": ds, "table_name": tname,
                                                        "last_refresh_time": _iso(NOW - timedelta(minutes=R.randint(5, 600))),
                                                        "refresh_watermark": None})
                all_tables.append((ref, tt, loc))
            if ds.startswith(("dw_", "mart_")):
                for r in range(R.randint(2, 8)):
                    lang = R.choice(["SQL", "SQL", "SQL", "JAVASCRIPT"]) if r % 3 else "SQL"
                    rtype = "PROCEDURE" if r % 4 == 0 else "FUNCTION"
                    feats = _view_features(proc=rtype == "PROCEDURE", js=lang == "JAVASCRIPT")
                    m["routines"].append({"routine_schema": ds, "routine_name": f"{'sp' if rtype == 'PROCEDURE' else 'fn'}_{ds}_{r}",
                                          "routine_type": rtype, "routine_body": "EXTERNAL" if lang == "JAVASCRIPT" else "SQL",
                                          "external_language": "JAVASCRIPT" if lang == "JAVASCRIPT" else None, "data_type": None,
                                          "created": "2024-05-01T00:00:00+00:00", "last_altered": "2026-08-01T00:00:00+00:00",
                                          "connection": None, "security_type": None, **feats})
            if ds == "dw_assistencial" and pid == "rx-dados-prd":
                m["dataset_level"]["row_access_policies"].append({"table_schema": ds, "table_name": names[0], "row_access_policy_name": "rap_unidade"})
        ctx.save_raw("bigquery", f"datasets_list__{pid}", ds_list)
        for loc, m in per_loc.items():
            m["column_types"] = [{"base_type": t, "columns": R.randint(200, 9000)} for t in ("STRING", "INT64", "NUMERIC", "TIMESTAMP", "DATE", "FLOAT64", "BOOL", "STRUCT", "ARRAY", "JSON")]
            ctx.save_raw("bigquery", f"metadata__{pid}__{loc}", m)
    hist = {}
    for pid, _, _ in PROJECTS:
        base = {"rx-dados-prd": 210e12, "rx-ingestao-prd": 95e12, "rx-bi-prd": 6e12, "rx-dados-hml": 12e12, "rx-ml-dev": 3e12,
                "rx-sandbox-analytics": 1.5e12}[pid]
        stored, upl = {}, {}
        for d in range(days, -1, -1):
            day = (NOW - timedelta(days=d)).date().isoformat()
            stored[day] = base * (1 - 0.0006 * d) * (1 + R.uniform(-0.001, 0.001))
            if pid == "rx-ingestao-prd":
                upl[day] = R.uniform(18e9, 26e9)
        hist[pid] = {"stored_bytes": stored, "uploaded_bytes": upl}
    _save(ctx, "bigquery", "storage_history", hist)
    for comp in ("datasets", "tables", "volumetry", "columns", "views", "routines", "security_metadata", "storage_history"):
        cov.ok("bigquery", comp, 10)
        cov.finalize("bigquery", comp, source=f"demo INFORMATION_SCHEMA ({comp})", history_window="180 dias" if comp == "storage_history" else None)
    cov.entry("bigquery", "partitions").status = "Not Assessed"
    cov.limitation("bigquery", "partitions", "Desabilitado (bigquery.collect_partitions=false)")
    cov.limitation("bigquery", "views", "sql.mode=features: definições SQL não exportadas; lineage de views depende de jobs/Data Lineage API")

    # ---------------------------------------------------------------- workloads
    physical_refs = [t for t, tt, _ in all_tables if tt in ("BASE TABLE", "MATERIALIZED VIEW", "VIEW")]
    principals = _principals()
    for pid, loc, share in (("rx-dados-prd", "southamerica-east1", 0.55), ("rx-bi-prd", "southamerica-east1", 0.25),
                            ("rx-ingestao-prd", "southamerica-east1", 0.15), ("rx-ml-dev", "US", 0.05)):
        w = _workloads(pid, loc, share, days, physical_refs, principals, red, ctx.config)
        ctx.save_raw("bigquery_workloads", f"workloads__{pid}__{loc}", w)
    for comp in ("jobs", "performance", "query_patterns", "consumers", "table_access", "lineage_edges", "capacity_usage", "slot_timeline"):
        cov.ok("bigquery_workloads", comp, 4)
        cov.finalize("bigquery_workloads", comp, source=f"demo JOBS_BY_PROJECT ({comp})", history_window=f"{days} dias")
    _save(ctx, "bigquery_capacity", "capacity", [
        {"project_id": "rx-dados-prd", "location": "southamerica-east1",
         "reservations": [{"name": "res-bi-prd", "slot_capacity": 200, "edition": "ENTERPRISE", "ignore_idle_slots": False,
                           "autoscale_max_slots": 400, "autoscale_current_slots": 0, "concurrency": 0, "creation_time": "2024-02-01T00:00:00Z"}],
         "commitments": [], "assignments": [{"reservation": "res-bi-prd", "assignee": "projects/rx-bi-prd", "job_type": "QUERY", "state": "ACTIVE"}],
         "bi_engine": {"size_bytes": 8 * 1024 ** 3, "preferred_tables": 4}}])
    cov.ok("bigquery_capacity", "reservations", 1)
    cov.finalize("bigquery_capacity", "reservations", source="demo Reservation API")
    cov.ok("bigquery_capacity", "bi_engine", 1)
    cov.finalize("bigquery_capacity", "bi_engine", source="demo biReservation")

    # ---------------------------------------------------------------- storage
    buckets = []
    for name, pid, sc, size, fmts, small in [
        ("rx-landing-arquivos-prd", "rx-ingestao-prd", "STANDARD", 48e12, {"parquet": 1200, "csv": 400, "json": 300}, 0.62),
        ("rx-datastream-cdc-prd", "rx-ingestao-prd", "STANDARD", 21e12, {"avro": 1800, "json": 50}, 0.81),
        ("rx-composer-dags-prd", "rx-dados-prd", "STANDARD", 2e9, {"py": 400, "sql": 900, "json": 80}, 0.99),
        ("rx-backup-bq-export", "rx-dados-prd", "COLDLINE", 140e12, {"avro": 900, "parquet": 300}, 0.05),
        ("rx-laudos-pdf-prd", "rx-ingestao-prd", "NEARLINE", 33e12, {"pdf": 1950, "dcm": 40}, 0.12),
        ("rx-ml-artifacts-dev", "rx-ml-dev", "STANDARD", 1.2e12, {"parquet": 300, "(sem extensão)": 500, "outros": 200}, 0.4),
        ("rx-temp-analistas", "rx-sandbox-analytics", "STANDARD", 3.8e12, {"csv": 900, "xlsx": 600, "parquet": 100}, 0.7),
        ("rx-logs-arquivo", "rx-dados-prd", "ARCHIVE", 75e12, {"gz": 2000}, 0.3)]:
        n_obj = int(size / R.uniform(5e6, 80e6))
        buckets.append({"project_id": pid, "bucket": name, "location": "SOUTHAMERICA-EAST1", "location_type": "region", "storage_class": sc,
                        "versioning": name.startswith("rx-backup"), "lifecycle_rules": 0 if name.startswith(("rx-landing", "rx-temp")) else 2,
                        "lifecycle_actions": [] if name.startswith(("rx-landing", "rx-temp")) else ["Delete", "SetStorageClass"],
                        "retention_seconds": 0, "retention_locked": False, "soft_delete_seconds": 604800, "cmek": name.startswith("rx-laudos"),
                        "uniform_bucket_level_access": name != "rx-temp-analistas", "public_access_prevention": "enforced" if "prd" in name else "inherited",
                        "autoclass": False, "hierarchical_namespace": False, "labels": {}, "created": "2023-05-01T00:00:00Z",
                        "bytes_by_class": {sc: size}, "total_bytes": size, "object_count": n_obj,
                        "public_members": ["allUsers"] if name == "rx-temp-analistas" else [],
                        "sample": {"objects_sampled": 2000, "bytes_sampled": int(size / n_obj * 2000), "formats": fmts,
                                   "size_distribution": {"<1MiB": int(2000 * small * 0.7), "1-16MiB": int(2000 * small * 0.3),
                                                         "16-128MiB": int(2000 * (1 - small) * 0.7), "128MiB-1GiB": int(2000 * (1 - small) * 0.3)},
                                   "small_files_sampled": int(2000 * small), "small_file_ratio": small, "avg_object_bytes": int(size / n_obj),
                                   "hive_partition_keys": {"dt": 1500} if "landing" in name else {},
                                   "table_format_markers": {"delta_log": 0, "iceberg_metadata": 0, "success_files": 12},
                                   "top_prefixes": [], "sample_truncated": True}})
    _save(ctx, "cloud_storage", "buckets", buckets)
    gm = {}
    for b in buckets:
        series = gm.setdefault(b["project_id"], {"daily_total_bytes": {}})["daily_total_bytes"]
        for d in range(days, -1, -1):
            day = (NOW - timedelta(days=d)).date().isoformat()
            series[day] = series.get(day, 0) + b["total_bytes"] * (1 - 0.0009 * d)
    _save(ctx, "cloud_storage", "monitoring", gm)
    for comp in ("buckets", "bucket_iam", "volume_metrics", "object_sampling"):
        cov.ok("cloud_storage", comp, len(buckets))
        cov.finalize("cloud_storage", comp, source=f"demo GCS ({comp})")

    # ---------------------------------------------------------------- pipelines
    pipes, transfers, edges = _pipelines(ctx, days)
    _save(ctx, "pipelines", "pipeline_inventory", pipes)
    _save(ctx, "pipelines", "transfers", transfers)
    _save(ctx, "pipelines", "dataform_edges", edges)
    _save(ctx, "pipelines", "composer", {"rx-dados-prd": [{"name": "projects/rx-dados-prd/locations/southamerica-east1/environments/composer-dados-prd",
                                                          "state": "RUNNING", "image_version": "composer-2.9.7-airflow-2.9.3", "environment_size": "ENVIRONMENT_SIZE_MEDIUM",
                                                          "private": True, "service_account": red.principal("sa-composer@rx-dados-prd.iam.gserviceaccount.com"),
                                                          "dag_bucket": "rx-composer-dags-prd", "dags": [{"dag_id": p["name"], "is_paused": p["state"] == "PAUSED"}
                                                                                                         for p in pipes if p["technology"].startswith("Cloud Composer")]}]})
    _save(ctx, "pipelines", "datastream", {"rx-ingestao-prd": [
        {"stream": "projects/rx-ingestao-prd/locations/southamerica-east1/streams/cdc-oracle-tasy", "state": "RUNNING", "source_type": "oracle",
         "source_host": red.host("db-tasy.redeexemplo.local"), "connectivity": "private", "objects_included": 214,
         "destination": "bigquery:rx-ingestao-prd.landing_oracle", "backfill": "all"},
        {"stream": "projects/rx-ingestao-prd/locations/southamerica-east1/streams/cdc-sqlserver-lab", "state": "RUNNING", "source_type": "sqlServer",
         "source_host": red.host("sqllab01.redeexemplo.local"), "connectivity": "ip-allowlist", "objects_included": 37,
         "destination": "bigquery:rx-ingestao-prd.landing_sqlserver", "backfill": "all"}]})
    _save(ctx, "pipelines", "dataproc", {"rx-ingestao-prd": [{"type": "cluster", "name": "dp-legado-hive", "region": "southamerica-east1", "state": "RUNNING",
                                                             "workers": 4, "worker_machine": "n2-highmem-8", "secondary_workers": 0,
                                                             "image_version": "2.1-debian11", "autoscaling": False, "optional_components": ["HIVE_WEBHCAT"]}]})
    _save(ctx, "pipelines", "bigquery_connections", {"rx-dados-prd": [{"name": "conn-cloudsql-portal", "location": "southamerica-east1", "type": "cloudSql",
                                                                      "cloudsql_type": "MYSQL", "target": "rx-ingestao-prd:southamerica-east1:mysql-portal-paciente",
                                                                      "has_credential": True},
                                                                     {"name": "conn-biglake-landing", "location": "southamerica-east1", "type": "cloudResource",
                                                                      "target": None, "has_credential": False}]})
    pcomps = ["bigquery_transfers", "bigquery_transfer_runs", "composer", "airflow_dags", "airflow_dag_runs", "dataflow", "dataflow_details", "dataproc",
              "dataproc_jobs", "dataproc_batches", "dataform", "dataform_workflows", "dataform_graph", "datafusion", "pubsub", "datastream", "scheduler",
              "workflows", "workflow_executions", "functions", "cloud_run", "bigquery_connections"]
    for comp in pcomps:
        cov.ok("pipelines", comp, 1 if comp != "datafusion" else 0)
        cov.finalize("pipelines", comp, source=f"demo {comp}", history_window=f"{days} dias")

    # ---------------------------------------------------------------- services
    services = {p: {"cloudsql": [], "alloydb": [], "spanner": [], "compute": [], "gke": [], "vertex_ai": {}, "dataplex": {}, "looker": []} for p, _, _ in PROJECTS}
    services["rx-ingestao-prd"]["cloudsql"] = [{"name": "mysql-portal-paciente", "engine": "MYSQL", "version": "MYSQL_8_0", "region": "southamerica-east1",
                                                "state": "RUNNABLE", "tier": "db-custom-8-32768", "availability": "REGIONAL", "disk_gb": 900, "public_ip": True,
                                                "private_network": True, "instance_type": "CLOUD_SQL_INSTANCE", "replicas": 1, "pitr": True, "cdc_ready": True}]
    services["rx-bi-prd"]["cloudsql"] = [{"name": "pg-metadados-bi", "engine": "POSTGRES", "version": "POSTGRES_15", "region": "southamerica-east1",
                                          "state": "RUNNABLE", "tier": "db-custom-2-8192", "availability": "ZONAL", "disk_gb": 50, "public_ip": False,
                                          "private_network": True, "instance_type": "CLOUD_SQL_INSTANCE", "replicas": 0, "pitr": False, "cdc_ready": False}]
    services["rx-bi-prd"]["compute"] = [{"name": f"vm-pbi-gateway-0{i}", "zone": "southamerica-east1-a", "machine_type": "n2-standard-8", "status": "RUNNING",
                                         "labels": {"funcao": "powerbi-gateway"}, "created": "2024-01-01", "os_hint": "windows-server-2022-dc", "gpus": 0,
                                         "service_account": red.principal("sa-powerbi-gateway@rx-bi-prd.iam.gserviceaccount.com")} for i in (1, 2)]
    services["rx-ingestao-prd"]["compute"] = [{"name": f"vm-etl-legado-0{i}", "zone": "southamerica-east1-b", "machine_type": "e2-standard-4",
                                               "status": "RUNNING" if i < 3 else "TERMINATED", "labels": {"funcao": "etl"}, "created": "2022-06-01",
                                               "os_hint": "ubuntu-2004", "gpus": 0, "service_account": None} for i in range(1, 5)]
    services["rx-ml-dev"]["vertex_ai"] = {"endpoints": [{"name": "risco-readmissao", "location": "us-central1", "deployed_models": 1}], "models": 4,
                                          "pipeline_jobs": 37, "workbench": [{"name": "wb-cientistas", "state": "ACTIVE"}]}
    services["rx-dados-prd"]["dataplex"] = {"lakes": [{"name": "lake-saude", "location": "southamerica-east1", "state": "ACTIVE"}],
                                            "data_scans": [{"name": f"dq-{x}", "location": "southamerica-east1", "type": "DATA_QUALITY", "state": "ACTIVE"} for x in ("atendimento", "faturamento")]}
    _save(ctx, "services", "services", services)
    for comp in ("cloudsql", "alloydb", "spanner", "compute", "gke", "vertex_ai", "dataplex", "looker"):
        cov.ok("services", comp, 1)
        cov.finalize("services", comp, source=f"demo {comp}")

    # ---------------------------------------------------------------- security
    from collectors.security import _member

    def binding(role, members):
        return {"role": role, "conditional": False, "members": [_member(ctx, m) for m in members]}

    sec = {"projects": {}, "vpc_service_controls": []}
    for pid, env, _ in PROJECTS:
        b = [binding("roles/owner", ["user:admin.cloud@redeexemplo.com.br", "group:gcp-admins@redeexemplo.com.br"]),
             binding("roles/editor", [f"serviceAccount:{800000000000 + PROJECTS.index((pid, env, _))}-compute@developer.gserviceaccount.com",
                                      "user:analista.terceiro@consultoria-exemplo.com"] if env != "prod" else
                     [f"serviceAccount:{800000000000 + PROJECTS.index((pid, env, _))}-compute@developer.gserviceaccount.com"]),
             binding("roles/bigquery.dataViewer", ["group:bi-usuarios@redeexemplo.com.br", f"serviceAccount:sa-powerbi-gateway@{pid}.iam.gserviceaccount.com"]),
             binding("roles/bigquery.jobUser", ["group:analistas@redeexemplo.com.br"])]
        if pid == "rx-sandbox-analytics":
            b.append(binding("roles/bigquery.dataViewer", ["allAuthenticatedUsers"]))
        sec["projects"][pid] = {
            "iam": {"bindings": b, "audit_configs": [{"service": "allServices", "log_types": ["ADMIN_READ"], "exempted_members": 0}]},
            "sa_keys": [{"service_account": red.principal(f"sa-etl@{pid}.iam.gserviceaccount.com"), "key_id_hash": red.pseudonym(f"k{pid}", "key"),
                         "age_days": 410 if env == "prod" else 35, "disabled": False, "origin": "GOOGLE_PROVIDED", "algorithm": "KEY_ALG_RSA_2048"}]
            if pid in ("rx-ingestao-prd", "rx-dados-hml") else [],
            "datasets": [{"dataset": f"{pid}.{ds}", "location": "southamerica-east1", "cmek": ds.startswith("raw_prontuario"),
                          "default_table_expiration_ms": None, "max_time_travel_hours": "168", "storage_billing_model": None,
                          "access": [{"role": "READER", "type": "group", "domain": "redeexemplo.com.br", "id": red.principal("group:bi@redeexemplo.com.br"), "public": False}],
                          "labels": {}} for ds, _, _ in DATASETS.get(pid, [])],
            "org_policies": {"iam.allowedPolicyMemberDomains": {"enforced": False, "allowed_values": [], "deny_all": False, "allow_all": False, "rules": 0},
                             "gcp.resourceLocations": {"enforced": False, "allowed_values": ["in:southamerica-east1-locations"] if env == "prod" else [],
                                                       "deny_all": False, "allow_all": False, "rules": 1 if env == "prod" else 0},
                             "iam.disableServiceAccountKeyCreation": {"enforced": env == "prod", "allowed_values": [], "deny_all": False, "allow_all": False, "rules": 1}},
            "governance": {"taxonomies": [{"name": "LGPD", "location": "southamerica-east1", "policy_tags": 6, "fine_grained_access": True}] if pid == "rx-dados-prd" else [],
                           "data_policies": [{"type": "DATA_MASKING_POLICY", "location": "southamerica-east1", "masking": "SHA256"}] if pid == "rx-dados-prd" else [],
                           "dlp_table_profiles": []}}
    _save(ctx, "security", "security", sec)
    for comp in ("iam_policies", "service_account_keys", "dataset_acl", "org_policies", "policy_tags", "data_masking", "dlp_profiles"):
        cov.ok("security", comp, 6)
        cov.finalize("security", comp, source=f"demo {comp}")
    from utils.http import PermissionDenied

    cov.fail("security", "vpc_service_controls", PermissionDenied(403, "The caller does not have permission",
                                                                  "https://accesscontextmanager.googleapis.com/v1/accessPolicies", "PERMISSION_DENIED"),
             scope="organizations/112233445566")
    cov.finalize("security", "vpc_service_controls", source="Access Context Manager")

    # ---------------------------------------------------------------- billing
    _save(ctx, "billing", "billing_export", _billing(days))
    cov.ok("billing", "billing_export", 7)
    cov.finalize("billing", "billing_export", source="demo Billing Export", history_window="12 meses", confidence="high")

    # ---------------------------------------------------------------- lineage API
    links = [{"source": f"bigquery:rx-ingestao-prd.landing_oracle.{x}", "target": f"bigquery:rx-dados-prd.stg_assistencial.{x}", "start": None, "end": None}
             for x in ("oracle_atendimento", "oracle_internacao")]
    _save(ctx, "lineage", "lineage_api_links", links)
    cov.ok("lineage", "lineage_api", 2)
    cov.finalize("lineage", "lineage_api", source="demo Data Lineage API")

    # ---------------------------------------------------------------- Power BI (parsers reais)
    sample_dir = Path(ctx.output_dir) / "demo_powerbi_artifacts"
    _write_powerbi_artifacts(sample_dir, physical_refs)
    ctx.config["powerbi"]["offline_path"] = str(sample_dir)
    saved_client, ctx.powerbi = ctx.powerbi, None
    pbi_mod.collect(ctx)
    ctx.powerbi = saved_client
    data = ctx.load_raw("powerbi", "powerbi")
    data["mode"] = "offline (demo) + refresh/atividade sintéticos"
    data["refresh"] = {"capacities": [{"id": "cap-1", "name": "Capacidade-P1", "sku": "P1", "state": "Active", "region": "Brazil South"}],
                       "gateways": [{"id": "gw-1", "name": "GW-BigQuery-PRD", "type": "Resource"}], "refreshables": [],
                       "refresh_history": [], "refresh_schedules": []}
    for m in data["semantic_models"]:
        for d in range(14):
            for slot in (5, 12, 18):
                start = (NOW - timedelta(days=d)).replace(hour=slot, minute=0, second=0)
                dur = R.uniform(300, 900) if m["name"] != "Faturamento Hospitalar" else R.uniform(1800, 4200)
                data["refresh"]["refresh_history"].append({"dataset_id": m["id"], "workspace_id": m.get("workspace_id"), "type": "Scheduled",
                                                           "status": "Failed" if R.random() < 0.04 else "Completed",
                                                           "start": start.isoformat().replace("+00:00", "Z"), "duration_s": dur})
        data["refresh"]["refresh_schedules"].append({"dataset_id": m["id"], "enabled": True, "days": 7, "times": 3, "timezone": "E. South America Standard Time"})
    views = {r["id"]: R.randint(0, 4000) for r in data["reports"]}
    data["activity"] = {"window_days": 28, "distinct_viewers": 612, "daily_viewers": {}, "views_by_report": views,
                        "views_by_hour_utc": {f"{h:02d}": int(400 * max(0, math.sin((h - 9) / 12 * math.pi)) + 10) for h in range(24)}}
    ctx.save_raw("powerbi", "powerbi", data)
    for comp in ("scanner", "refresh", "activity"):
        cov.ok("powerbi", comp, 1)
        cov.finalize("powerbi", comp, source="demo")


# ---------------------------------------------------------------- helpers
def _view_features(proc: bool = False, js: bool = False) -> dict:
    from analyzers.sql_complexity import FEATURE_NAMES

    f = {"f_chars": R.randint(400, 12000)}
    for name in FEATURE_NAMES:
        f[f"f_{name}"] = 0
    heavy = R.random() < 0.2
    f["f_chars"] = R.randint(2500, 15000) if heavy else R.randint(200, 3000)
    f["f_joins"] = R.choice([4, 6, 9, 14]) if heavy else R.choice([0, 0, 1, 2, 3])
    f["f_subqueries"] = R.choice([1, 2, 5]) if heavy else R.choice([0, 0, 0, 1])
    f["f_ctes"] = R.choice([3, 5, 9]) if heavy else R.choice([0, 0, 1, 2])
    f["f_window_functions"] = R.choice([0, 1, 3]) if heavy else R.choice([0, 0, 0, 1])
    f["f_select_star"] = R.choice([0, 0, 0, 1])
    f["f_select_distinct"] = R.choice([0, 1, 2]) if heavy else R.choice([0, 0, 0, 1])
    f["f_casts"] = R.randint(5, 25) if heavy else R.randint(0, 6)
    f["f_bq_specific_functions"] = R.randint(2, 12) if heavy else R.randint(0, 3)
    f["f_unnest"] = R.choice([0, 1, 2]) if heavy else 0
    f["f_qualify"] = R.choice([0, 1]) if heavy else 0
    f["f_or_in_join"] = R.choice([0, 0, 1]) if heavy else 0
    f["f_case_when"] = R.randint(5, 30) if heavy else R.randint(0, 6)
    f["f_group_by"] = R.randint(1, 4) if heavy else R.randint(0, 1)
    if proc:
        f["f_procedural"] = R.randint(1, 6)
        f["f_merge"] = R.randint(0, 3)
        f["f_dynamic_sql"] = R.choice([0, 0, 1])
    if js:
        f["f_js_udf"] = 1
    return f


def _principals() -> list[tuple[str, dict]]:
    out = [("sa-dataform@rx-dados-prd.iam.gserviceaccount.com", {"kind": "dw", "weight": 30}),
           ("sa-composer@rx-dados-prd.iam.gserviceaccount.com", {"kind": "etl", "weight": 18}),
           ("service-800000000000@gcp-sa-bigquerydatatransfer.iam.gserviceaccount.com", {"kind": "etl", "weight": 10}),
           ("sa-powerbi-gateway@rx-bi-prd.iam.gserviceaccount.com", {"kind": "bi", "weight": 22}),
           ("sa-looker-studio@rx-bi-prd.iam.gserviceaccount.com", {"kind": "bi", "weight": 3}),
           ("sa-app-portal@rx-ingestao-prd.iam.gserviceaccount.com", {"kind": "app", "weight": 6}),
           ("sa-vertex-pipelines@rx-ml-dev.iam.gserviceaccount.com", {"kind": "ml", "weight": 3}),
           ("sa-etl@rx-ingestao-prd.iam.gserviceaccount.com", {"kind": "etl", "weight": 6})]
    for i in range(40):
        out.append((f"analista{i:02d}@redeexemplo.com.br", {"kind": "adhoc", "weight": 0.4}))
    out.append(("consultor.externo@consultoria-exemplo.com", {"kind": "adhoc", "weight": 0.3}))
    return out


def _workloads(pid, loc, share, days, refs, principals, red, config) -> dict:
    from collectors.bigquery_workloads import _anonymize

    hourly, timeline, hist, bi_hourly = [], [], {}, []
    start = NOW - timedelta(days=days)
    for d in range(days):
        day = start + timedelta(days=d)
        dom = day.day
        wk = day.weekday()
        month_edge = 1.35 if dom >= 26 or dom <= 5 else 1.0
        weekend = 0.35 if wk >= 5 else 1.0
        for h in range(24):
            ts = day.replace(hour=h, minute=0, second=0, microsecond=0)
            local_h = (h - 3) % 24
            diurnal = 0.25 + max(0.0, math.sin((local_h - 6) / 14 * math.pi)) if 6 <= local_h <= 20 else 0.25
            batch_peak = 2.5 if local_h in (1, 2, 3, 4) else 1.0
            for jt, pr, base in (("QUERY", "INTERACTIVE", 260), ("QUERY", "BATCH", 40), ("LOAD", "INTERACTIVE", 25), ("COPY", "INTERACTIVE", 3),
                                 ("EXTRACT", "BATCH", 1)):
                f = (diurnal * weekend if pr == "INTERACTIVE" and jt == "QUERY" else batch_peak) * month_edge * share
                jobs = max(0, int(R.gauss(base * f, base * f * 0.15)))
                if not jobs:
                    continue
                stmts = int(jobs * (1.3 if pr == "BATCH" else 1.02))
                bytes_b = int(jobs * (R.uniform(0.6e9, 2.5e9) if jt == "QUERY" else 0))
                slot = int(jobs * (R.uniform(0.5e6, 2.2e6) if jt == "QUERY" else R.uniform(2e4, 6e4)))
                run = int(jobs * (R.uniform(2500, 9000) if pr == "INTERACTIVE" else R.uniform(30000, 240000)))
                hourly.append({"hour": _iso(ts), "job_type": jt, "priority": pr, "jobs": jobs, "statements": stmts,
                               "scripts": int(jobs * 0.05) if pr == "BATCH" else 0, "child_jobs": stmts - jobs,
                               "failed": int(jobs * R.uniform(0.002, 0.02)), "cache_hits": int(jobs * (0.28 if pr == "INTERACTIVE" else 0.02)),
                               "bytes_processed": int(bytes_b * 1.05), "bytes_billed": bytes_b,
                               "on_demand_bytes_billed": 0 if pid == "rx-bi-prd" else bytes_b, "slot_ms": slot,
                               "sum_run_ms": run, "max_run_ms": int(run / jobs * R.uniform(4, 30)), "sum_queue_ms": int(jobs * R.uniform(50, 600)),
                               "principals": R.randint(3, 25)})
                if pr == "INTERACTIVE" and jt == "QUERY":
                    for bkt in range(15):
                        hist_key = (jt, pr, bkt)
                        hist[hist_key] = hist.get(hist_key, 0) + int(jobs * [0.02, 0.05, 0.08, 0.16, 0.2, 0.2, 0.12, 0.09, 0.04, 0.02, 0.012, 0.005, 0.002, 0.001, 0][bkt])
                elif jt == "QUERY":
                    for bkt in range(15):
                        hist[(jt, pr, bkt)] = hist.get((jt, pr, bkt), 0) + int(jobs * [0, 0, 0.01, 0.03, 0.05, 0.1, 0.15, 0.25, 0.18, 0.12, 0.07, 0.03, 0.01, 0.003, 0.001][bkt])
            tot_slot = sum(x["slot_ms"] for x in hourly[-5:] if x["hour"] == _iso(ts))
            avg = tot_slot / 3.6e6
            timeline.append({"hour": _iso(ts), "avg_slots": round(avg, 2), "peak_minute_slots": round(avg * R.uniform(2.5, 6), 1),
                             "p95_active_minute_slots": round(avg * 2.2, 1), "peak_concurrent_jobs": int(R.uniform(3, 12) * diurnal * month_edge * share * 4) + 1,
                             "avg_concurrent_jobs_active_minutes": round(R.uniform(1, 4) * share * 4, 2), "active_minutes": 60})
            if pid == "rx-bi-prd" and local_h in (2, 9, 15):
                bi_hourly.append({"hour": _iso(ts), "principal": "sa-powerbi-gateway@rx-bi-prd.iam.gserviceaccount.com", "jobs": R.randint(40, 300),
                                  "bytes_billed": R.randint(10**10, 10**11), "slot_ms": R.randint(10**6, 10**7),
                                  "refs": ["rx-bi-prd.semantic_bi.bi_fato_receita", "rx-dados-prd.dw_faturamento.faturamento_faturamento",
                                           "rx-dados-prd.mart_bi.bi_painel"]})
    pct = [{"job_type": "QUERY", "priority": "INTERACTIVE", "jobs": sum(x["jobs"] for x in hourly if x["priority"] == "INTERACTIVE" and x["job_type"] == "QUERY"),
            "avg_run_ms": 5400, "p50_ms": 2100, "p90_ms": 11800, "p95_ms": 24500, "p99_ms": 96000, "avg_queue_ms": 220, "p95_queue_ms": 1400},
           {"job_type": "QUERY", "priority": "BATCH", "jobs": sum(x["jobs"] for x in hourly if x["priority"] == "BATCH"),
            "avg_run_ms": 98000, "p50_ms": 41000, "p90_ms": 260000, "p95_ms": 480000, "p99_ms": 1500000, "avg_queue_ms": 2200, "p95_queue_ms": 18000}]
    sample_refs = [r for r in refs if r.startswith(pid)] or refs
    fps = []
    for i in range(120):
        runs = int(math.exp(R.gauss(4, 2))) + 1
        rr = R.sample(sample_refs, min(len(sample_refs), R.randint(1, 4)))
        p, meta = R.choice(principals[:8]) if R.random() < 0.8 else R.choice(principals)
        feats = _view_features(proc=False)
        st = "SELECT" if meta["kind"] in ("bi", "adhoc", "app") else R.choice(["MERGE", "INSERT", "CREATE_TABLE_AS_SELECT", "SELECT"])
        if st == "MERGE":
            feats["f_merge"] = 1
        bpr = R.uniform(0.05e9, 400e9) if R.random() < 0.15 else R.uniform(0.01e9, 8e9)
        fps.append({"fp": f"{R.getrandbits(64):016x}", "statement_type": st, "priority": "BATCH" if meta["kind"] in ("etl", "dw") else "INTERACTIVE",
                    "runs": runs, "principals": R.randint(1, 30) if meta["kind"] == "adhoc" else 1, "sample_principal": p,
                    "bytes_billed": int(runs * bpr), "bytes_processed": int(runs * bpr * 1.02), "slot_ms": int(runs * bpr / 1e9 * R.uniform(8e3, 40e3)),
                    "avg_run_ms": R.uniform(800, 60000) if st == "SELECT" else R.uniform(20000, 900000), "max_run_ms": R.uniform(60000, 3600000),
                    "p95_run_ms": R.uniform(5000, 900000), "failed": int(runs * R.choice([0] * 12 + [0.01, 0.02, 0.3])), "cache_hits": int(runs * R.choice([0, 0.05, 0.4])),
                    "first_seen": _iso(NOW - timedelta(days=days)), "last_seen": _iso(NOW - timedelta(hours=R.randint(1, 200))),
                    "active_days": min(days, R.randint(1, days)), "refs": rr, "label_kv": ["airflow-dag=dag_carga_assistencial"] if meta["kind"] == "etl" else [],
                    "sample_destination": rr[0] if st != "SELECT" else None, **feats})
    tot_bytes = sum(x["bytes_billed"] for x in hourly if x["job_type"] == "QUERY")
    tot_slot = sum(x["slot_ms"] for x in hourly if x["job_type"] == "QUERY")
    tot_q = sum(x["statements"] for x in hourly if x["job_type"] == "QUERY")
    sb, ss, sr = (sum(f[k] for f in fps) or 1 for k in ("bytes_billed", "slot_ms", "runs"))
    for f in fps:  # as top fingerprints representam ~75% do consumo da região (consistente com os totais)
        f["runs"] = max(1, int(f["runs"] / sr * tot_q * 0.6))
        f["bytes_billed"] = int(f["bytes_billed"] / sb * tot_bytes * 0.75)
        f["bytes_processed"] = int(f["bytes_billed"] * 1.03)
        f["slot_ms"] = int(f["slot_ms"] / ss * tot_slot * 0.75)
        f["failed"] = min(f["failed"], f["runs"])
        f["cache_hits"] = min(f["cache_hits"], f["runs"])
    total_jobs = sum(x["jobs"] for x in hourly)
    tot_all_slot = sum(x["slot_ms"] for x in hourly)
    tot_all_bytes = sum(x["bytes_billed"] for x in hourly)
    plist = []
    for p, meta in principals:
        wgt = meta["weight"] / 100 * total_jobs
        jobs = int(wgt * R.uniform(0.7, 1.3))
        if jobs <= 0:
            continue
        kind = meta["kind"]
        plist.append({"principal": p, "jobs": jobs, "query_jobs": int(jobs * (0.6 if kind == "etl" else 0.98)),
                      "load_jobs": int(jobs * 0.35) if kind == "etl" else 0, "extract_copy_jobs": int(jobs * 0.02) if kind == "etl" else 0,
                      "write_statements": int(jobs * (0.8 if kind == "dw" else 0.4 if kind == "etl" else 0.01)),
                      "ml_statements": int(jobs * 0.05) if kind == "ml" else 0, "select_statements": int(jobs * (0.95 if kind in ("bi", "adhoc", "app") else 0.2)),
                      "bytes_billed": int(jobs * R.uniform(0.3e9, 4e9)) if kind != "app" else int(jobs * 2e7),
                      "slot_ms": int(jobs * R.uniform(8e3, 60e3)) * (3 if kind in ("dw", "etl") else 1),
                      "distinct_fingerprints": R.randint(5, 40) if kind != "adhoc" else R.randint(50, 600), "cache_hits": int(jobs * 0.2),
                      "active_hours": 24 if kind in ("etl", "dw", "app") else R.randint(8, 14), "active_days": days if kind != "adhoc" else R.randint(5, days),
                      "first_seen": _iso(NOW - timedelta(days=days)), "last_seen": _iso(NOW)})
    sp, bp = (sum(p[k] for p in plist) or 1 for k in ("slot_ms", "bytes_billed"))
    for p in plist:
        p["slot_ms"] = int(p["slot_ms"] / sp * tot_all_slot)
        p["bytes_billed"] = int(p["bytes_billed"] / bp * tot_all_bytes)
    labels = [{"kv": "airflow-dag=dag_carga_assistencial", "principal": "sa-composer@rx-dados-prd.iam.gserviceaccount.com", "jobs": 5400, "slot_ms": 9e8,
               "bytes_billed": 4e12, "last_seen": _iso(NOW)},
              {"kv": "data_source_id=scheduled_query", "principal": "service-800000000000@gcp-sa-bigquerydatatransfer.iam.gserviceaccount.com", "jobs": 2100,
               "slot_ms": 3e8, "bytes_billed": 1e12, "last_seen": _iso(NOW)},
              {"kv": "looker_studio_datasource_id=abc123", "principal": "sa-looker-studio@rx-bi-prd.iam.gserviceaccount.com", "jobs": 900, "slot_ms": 1e7,
               "bytes_billed": 5e10, "last_seen": _iso(NOW)}]
    tables_read = []
    bi_first = [r for r in sample_refs if any(x in r for x in (".mart_bi.", ".semantic_bi.", ".dw_faturamento.", ".dw_assistencial."))]
    for r in (bi_first + [x for x in sample_refs if x not in bi_first])[:120]:
        jobs = int(math.exp(R.gauss(5, 1.8)))
        bi = r in bi_first
        ps = ["sa-powerbi-gateway@rx-bi-prd.iam.gserviceaccount.com"] if bi else []
        ps += [R.choice(principals)[0] for _ in range(R.randint(1, 5))]
        tables_read.append({"table_ref": r, "jobs": jobs, "principals": len(set(ps)), "last_read": _iso(NOW - timedelta(hours=R.randint(1, 72))),
                            "first_read": _iso(NOW - timedelta(days=days)), "attributed_bytes_billed": int(jobs * 2e9), "principal_sample": sorted(set(ps))})
    writes, edges = [], []
    layers = [("landing_oracle", "stg_assistencial"), ("stg_assistencial", "dw_assistencial"), ("raw_faturamento", "dw_faturamento"),
              ("dw_faturamento", "mart_bi"), ("dw_assistencial", "mart_bi"), ("mart_bi", "semantic_bi")]
    for src_ds, dst_ds in layers:
        srcs = [r for r in refs if f".{src_ds}." in r][:8]
        dsts = [r for r in refs if f".{dst_ds}." in r][:8]
        for s in srcs:
            for t in R.sample(dsts, min(len(dsts), 2)) if dsts else []:
                edges.append({"source_ref": s, "target_ref": t, "jobs": R.randint(30, 400), "last_seen": _iso(NOW), "statement_type": "MERGE", "active_days": days})
        for t in dsts:
            writes.append({"table_ref": t, "job_type": "QUERY", "statement_type": "MERGE", "jobs": R.randint(30, 400), "slot_ms": 3e8, "bytes_processed": 2e11,
                           "last_write": _iso(NOW), "active_days": days, "principals": ["sa-composer@rx-dados-prd.iam.gserviceaccount.com"],
                           "label_kv": [f"airflow-dag=dag_{dst_ds}"] if dst_ds != "mart_bi" else ["dataform_repository_id=dw-saude"]})
    res = {"project_id": pid, "location": loc, "window_days": days, "hourly": hourly,
           "duration_histogram": [{"job_type": k[0], "priority": k[1], "run_bucket": k[2], "queue_bucket": max(0, k[2] - 3), "jobs": v} for k, v in hist.items() if v],
           "percentiles": pct, "fingerprints": fps,
           "fingerprint_summary": [{"distinct_fingerprints": 4200, "recurring_fingerprints": 1900, "fingerprints_7plus_days": 820,
                                    "query_statements": sum(x["statements"] for x in hourly if x["job_type"] == "QUERY"),
                                    "runs_of_recurring": int(0.86 * sum(x["statements"] for x in hourly if x["job_type"] == "QUERY"))}],
           "principals": plist, "labels": labels, "tables_read": tables_read, "writes": writes, "edges": edges,
           "by_reservation": [{"reservation_id": "rx-dados-prd:southamerica-east1.res-bi-prd" if pid == "rx-bi-prd" else "ON_DEMAND", "edition": "ENTERPRISE" if pid == "rx-bi-prd" else "",
                               "jobs": total_jobs, "slot_ms": sum(x["slot_ms"] for x in hourly), "bytes_billed": sum(x["bytes_billed"] for x in hourly)}],
           "timeline": timeline, "bi_hourly": bi_hourly}
    rules = config.get("workload_rules", {})
    pbi_patterns = list(config.get("powerbi", {}).get("principal_patterns", [])) + list(rules.get("bi_principals", []))

    class _Ctx:  # adaptador mínimo para reutilizar a anonimização real do collector
        pass

    c = _Ctx()
    c.redactor, c.config = red, config
    _anonymize(c, res, pbi_patterns, rules)
    return res


def _pipelines(ctx: Context, days: int):
    red = ctx.redactor
    pipes, transfers = [], {"rx-dados-prd": []}
    for i in range(26):
        tgt_ds = R.choice(["mart_bi", "dw_faturamento", "dw_assistencial"])
        sched = R.choice(["every 24 hours", "every day 03:00", "every 1 hours", "every mon 06:00", "every 6 hours"])
        cls = R.choice(["Baixa", "Média", "Média", "Alta", "Muito alta"])
        runs = [{"state": "SUCCEEDED" if R.random() > 0.05 else "FAILED", "duration_s": R.uniform(30, 1800), "start": _iso(NOW - timedelta(days=k))} for k in range(30)]
        from collectors.pipelines import _run_stats

        rec = _record(id=f"projects/800000000000/locations/southamerica-east1/transferConfigs/{i:04d}", name=f"sq_{tgt_ds}_{i:02d}",
                      technology="BigQuery DTS (scheduled_query)", project_id="rx-dados-prd", location="southamerica-east1",
                      sources=[f"rx-dados-prd.{R.choice(['dw_faturamento', 'dw_assistencial', 'stg_assistencial'])}.tabela_{R.randint(1, 30)}"],
                      targets=[f"rx-dados-prd.{tgt_ds}.agg_{i:02d}"], schedule=sched, state="SUCCEEDED",
                      load_strategy=R.choice(["full load", "incremental", "incremental (MERGE)"]), confidence="high",
                      service_account=red.principal("sa-etl@rx-dados-prd.iam.gserviceaccount.com"), **_run_stats(runs))
        rec["sql_complexity"] = cls
        pipes.append(rec)
        transfers["rx-dados-prd"].append({"name": rec["id"], "display_name": rec["name"], "data_source_id": "scheduled_query",
                                          "destination_dataset": tgt_ds, "schedule": sched, "disabled": False, "state": "SUCCEEDED",
                                          "params": {"write_disposition": "WRITE_TRUNCATE"}, "param_keys": ["query", "write_disposition"],
                                          "sql_analysis": {"complexity": cls, "score": {"Baixa": 6, "Média": 18, "Alta": 45, "Muito alta": 90}[cls],
                                                           "anti_patterns": R.sample(["SELECT *", "CROSS JOIN", "SELECT DISTINCT excessivo", "JOINs complexos (>=10)"], R.randint(0, 2)),
                                                           "referenced_tables": rec["sources"], "features": {}}})
    from collectors.pipelines import _run_stats

    for dag, rpd, ops in [("dag_carga_assistencial", 24, ["BigQueryInsertJobOperator x22", "GCSToBigQueryOperator x6", "PythonOperator x4"]),
                          ("dag_stg_assistencial", 24, ["BigQueryInsertJobOperator x14"]), ("dag_dw_assistencial", 4, ["BigQueryInsertJobOperator x31", "DataformCreateWorkflowInvocationOperator x1"]),
                          ("dag_dw_faturamento", 4, ["BigQueryInsertJobOperator x27", "SFTPToGCSOperator x2"]), ("dag_fechamento_mensal", 1 / 30, ["BigQueryInsertJobOperator x48", "EmailOperator x1"]),
                          ("dag_export_ans", 1 / 30, ["BigQueryToGCSOperator x6", "SFTPOperator x2"]), ("dag_ingestao_sharepoint", 2, ["PythonOperator x5", "GCSToBigQueryOperator x5"]),
                          ("dag_reprocessamento", None, ["BigQueryInsertJobOperator x9"])]:
        runs = [{"state": "success" if R.random() > (0.18 if dag == "dag_ingestao_sharepoint" else 0.03) else "failed", "duration_s": R.uniform(120, 5400),
                 "start": _iso(NOW - timedelta(hours=k * 6))} for k in range(40)]
        pipes.append(_record(id=f"composer-dados-prd/dags/{dag}", name=dag, technology="Cloud Composer (Airflow)", project_id="rx-dados-prd",
                             location="southamerica-east1", runs_per_day=rpd, state="ACTIVE", retry=2, dependencies=ops,
                             sources=["external:sftp:parceiros"] if "sftp" in " ".join(ops).lower() else (["external:sharepoint"] if "sharepoint" in dag else []),
                             targets=[f"rx-dados-prd.{dag.replace('dag_', '')}" if dag.startswith(("dag_dw", "dag_stg")) else "rx-dados-prd.dw_assistencial"],
                             confidence="high", code_ref=red.pseudonym(dag, "dagfile"), **_run_stats(runs)))
    for name, typ, src in [("df-eventos-app", "JOB_TYPE_STREAMING", "pubsub:eventos-app-paciente"), ("df-resultados-exames", "JOB_TYPE_STREAMING", "pubsub:resultados-exames"),
                           ("df-carga-laudos", "JOB_TYPE_BATCH", "gcs:rx-laudos-pdf-prd")]:
        streaming = typ == "JOB_TYPE_STREAMING"
        pipes.append(_record(id=f"dataflow:rx-ingestao-prd:{name}", name=name, technology="Dataflow", project_id="rx-ingestao-prd",
                             location="southamerica-east1", sources=[src], targets=["rx-ingestao-prd.eventos_app.app_evento"] if streaming else ["rx-ingestao-prd.landing_oracle"],
                             state="JOB_STATE_RUNNING" if streaming else "JOB_STATE_DONE", processing="streaming" if streaming else "batch",
                             load_strategy="streaming" if streaming else "full load", runs_per_day=None if streaming else 1.0,
                             runs_observed=1 if streaming else days, avg_duration_s=None if streaming else 2400, max_duration_s=None if streaming else 7200,
                             failure_rate=0.0 if streaming else 0.02, confidence="high"))
    for name, src, ds, n in [("cdc-oracle-tasy", "oracle", "landing_oracle", 214), ("cdc-sqlserver-lab", "sqlServer", "landing_sqlserver", 37)]:
        pipes.append(_record(id=f"projects/rx-ingestao-prd/locations/southamerica-east1/streams/{name}", name=name, technology="Datastream (CDC)",
                             project_id="rx-ingestao-prd", location="southamerica-east1", sources=[f"external:{src}:{red.host(name)}"],
                             targets=[f"bigquery:rx-ingestao-prd.{ds}"], processing="streaming", load_strategy="CDC", state="RUNNING",
                             frequency="contínua (freshness 900s)", confidence="high", notes=[f"Objetos incluídos: {n}"]))
    for t in ("agenda-consultas", "eventos-app-paciente", "resultados-exames"):
        pipes.append(_record(id=f"projects/rx-ingestao-prd/subscriptions/{t}-bq", name=f"{t}-bq", technology="Pub/Sub (bigquery subscription)",
                             project_id="rx-ingestao-prd", location="global", sources=[f"pubsub:{t}"], targets=["rx-ingestao-prd.eventos_app.app_evento"],
                             processing="streaming", load_strategy="streaming", frequency="contínua (streaming)", state="ACTIVE", confidence="high"))
    for i, (sched, tgt) in enumerate([("0 2 * * *", "workflows"), ("*/30 * * * *", "cloudrun"), ("0 6 1 * *", "workflows"), ("0 */4 * * *", "dataflow"),
                                      ("15 3 * * 1-5", "composer")]):
        pipes.append(_record(id=f"sch-{i}", name=f"sch-{tgt}-{i}", technology="Cloud Scheduler", project_id="rx-dados-prd", location="southamerica-east1",
                             schedule=sched, state="ENABLED", targets=[tgt], dependencies=[tgt], retry=3, confidence="high"))
    pipes.append(_record(id="wf-fechamento-mensal", name="wf-fechamento-mensal", technology="Workflows", project_id="rx-dados-prd", location="southamerica-east1",
                         state="ACTIVE", dependencies=["bigquery", "dataform", "http"], runs_per_day=0.03, runs_observed=3, avg_duration_s=5400, max_duration_s=8100,
                         failure_rate=0.0, confidence="medium"))
    pipes.append(_record(id="projects/rx-dados-prd/locations/southamerica-east1/repositories/dw-saude", name="dw-saude", technology="Dataform",
                         project_id="rx-dados-prd", location="southamerica-east1", schedule="0 4 * * *", state="ACTIVE",
                         sources=["rx-dados-prd.stg_assistencial", "rx-dados-prd.raw_faturamento"], targets=["rx-dados-prd.dw_assistencial", "rx-dados-prd.mart_bi"],
                         load_strategy="incremental", runs_observed=days, avg_duration_s=1900, max_duration_s=4100, failure_rate=0.04, confidence="high"))
    for f in ("fn-webhook-convenios", "fn-validacao-arquivos", "fn-notifica-falhas"):
        pipes.append(_record(id=f, name=f, technology="Cloud Functions (evento)", project_id="rx-ingestao-prd", location="southamerica-east1", state="ACTIVE",
                             sources=["evento:google.cloud.storage.object.v1.finalized"] if "arquivos" in f else ["evento:google.cloud.pubsub.topic.v1.messagePublished"],
                             processing="event-driven", frequency="orientada a eventos", confidence="medium"))
    pipes.append(_record(id="dataproc:rx-ingestao-prd:hive-legado", name="hive-legado-diario", technology="Dataproc (hiveJob)", project_id="rx-ingestao-prd",
                         location="southamerica-east1", runs_per_day=1, runs_observed=days, avg_duration_s=3100, max_duration_s=9800, failure_rate=0.12, confidence="medium"))
    edges = [{"source": "rx-dados-prd.stg_assistencial.assistencial_atendimento", "target": "rx-dados-prd.dw_assistencial.assistencial_fato_atendimento",
              "via": "dataform", "repository": "dw-saude", "kind": "INCREMENTAL_TABLE"},
             {"source": "rx-dados-prd.dw_assistencial.assistencial_fato_atendimento", "target": "rx-dados-prd.mart_bi.bi_painel",
              "via": "dataform", "repository": "dw-saude", "kind": "TABLE"}]
    return pipes, transfers, edges


def _billing(days: int) -> dict:
    months = []
    d = NOW.replace(day=1)
    for _ in range(13):
        months.append(f"{d.year}{d.month:02d}")
        d = (d - timedelta(days=1)).replace(day=1)
    months = sorted(months)
    services = {"BigQuery": 61000, "Cloud Storage": 9800, "Compute Engine": 7200, "Cloud Composer": 3900, "Dataflow": 5200, "Datastream": 2600,
                "Cloud Pub/Sub": 900, "Cloud SQL": 2100, "Vertex AI": 1800, "Dataproc": 1400, "Networking": 1250, "Cloud Run": 300,
                "Cloud Functions": 120, "Dataform": 0, "Cloud Monitoring": 380, "Cloud Logging": 640, "Support": 1500}
    by_sm, by_pm, by_sku_m, credits, ctype, region = [], [], [], [], [], []
    for i, m in enumerate(months):
        growth = 1 + 0.018 * i
        partial = 0.8 if i == len(months) - 1 else 1
        for s, base in services.items():
            c = base * growth * partial * R.uniform(0.92, 1.08)
            by_sm.append({"month": m, "service": s, "cost": round(c, 2), "credits": round(-c * (0.12 if s == "BigQuery" else 0.04), 2), "currency": "BRL"})
        for p, sh in (("rx-dados-prd", 0.48), ("rx-ingestao-prd", 0.27), ("rx-bi-prd", 0.12), ("rx-ml-dev", 0.05), ("rx-dados-hml", 0.05), ("rx-sandbox-analytics", 0.03)):
            tot = sum(services.values()) * growth * partial
            by_pm.append({"month": m, "project_id": p, "cost": round(tot * sh, 2), "credits": round(-tot * sh * 0.08, 2)})
        for sku, base in (("Analysis (southamerica-east1)", 34000), ("Enterprise Edition (southamerica-east1)", 14000),
                          ("Long Term Logical Storage", 3200), ("Active Physical Storage", 7400), ("Storage Write API", 1900), ("BI Engine", 500)):
            by_sku_m.append({"month": m, "service": "BigQuery", "sku": sku, "cost": round(base * growth * partial, 2), "credits": 0})
        by_sku_m.append({"month": m, "service": "Networking", "sku": "Network Internet Egress from South America to Americas", "cost": round(900 * growth * partial, 2), "credits": 0})
        by_sku_m.append({"month": m, "service": "Cloud Composer", "sku": "Cloud Composer 2 Compute vCPU", "cost": round(3000 * growth * partial, 2), "credits": 0})
        by_sku_m.append({"month": m, "service": "Datastream", "sku": "Datastream CDC data processed", "cost": round(2600 * growth * partial, 2), "credits": 0})
        credits.append({"month": m, "credit_type": "COMMITTED_USAGE_DISCOUNT", "amount": round(-7800 * growth, 2)})
        credits.append({"month": m, "credit_type": "PROMOTION", "amount": -200.0})
        ctype.append({"month": m, "cost_type": "regular", "cost": round(sum(services.values()) * growth * partial, 2)})
        region.append({"month": m, "region": "southamerica-east1", "cost": round(sum(services.values()) * growth * partial * 0.9, 2)})
    by_sku = [{"service": "BigQuery", "sku": "Analysis (southamerica-east1)", "cost": 520000.0, "credits": -62000.0, "usage_amount": 0, "usage_unit": "byte"},
              {"service": "BigQuery", "sku": "Enterprise Edition (southamerica-east1)", "cost": 210000.0, "credits": 0, "usage_amount": 0, "usage_unit": "slot-hour"},
              {"service": "Cloud Storage", "sku": "Standard Storage São Paulo", "cost": 98000.0, "credits": 0, "usage_amount": 0, "usage_unit": "gibibyte month"},
              {"service": "Compute Engine", "sku": "N2 Instance Core running in São Paulo", "cost": 61000.0, "credits": 0, "usage_amount": 0, "usage_unit": "hour"},
              {"service": "Dataflow", "sku": "Streaming data processed", "cost": 44000.0, "credits": 0, "usage_amount": 0, "usage_unit": "gibibyte"},
              {"service": "Cloud Composer", "sku": "Cloud Composer 2 Compute vCPU", "cost": 41000.0, "credits": 0, "usage_amount": 0, "usage_unit": "hour"},
              {"service": "Datastream", "sku": "Datastream CDC data processed", "cost": 33000.0, "credits": 0, "usage_amount": 0, "usage_unit": "gibibyte"},
              {"service": "Networking", "sku": "Network Internet Egress from South America to Americas", "cost": 14000.0, "credits": 0, "usage_amount": 0, "usage_unit": "gibibyte"}]
    res = {"by_service_month": by_sm, "by_project_month": by_pm, "by_sku": by_sku, "by_sku_month": by_sku_m, "credits_by_type": credits,
           "cost_type": ctype, "by_region_month": region,
           "top_resources": [{"service": "Cloud Composer", "resource_name": "composer-dados-prd", "project_id": "rx-dados-prd", "cost": 3900.0, "credits": 0}]}
    table = "rx-dados-prd.finops.gcp_billing_export_resource_v1_01A2B3_C4D5E6_F7G8H9"
    return {"exports": [{"table": table, "project_id": "rx-dados-prd", "location": "US", "detailed": True}], "results": {table: res}, "months": 12}


def _write_powerbi_artifacts(root: Path, refs: list[str]) -> None:
    """Gera 2 PBIP (TMDL) + 1 JSON de scanner com 5 modelos e 9 relatórios."""
    root.mkdir(parents=True, exist_ok=True)
    tab = "\t"
    # PBIP 1: Painel Assistencial (Import, BigQuery com navegação explícita + SharePoint)
    sm = root / "Painel Assistencial.SemanticModel" / "definition"
    (sm / "tables").mkdir(parents=True, exist_ok=True)
    (sm / "tables" / "Atendimentos.tmdl").write_text(f"""table Atendimentos
{tab}lineageTag: 11111111-aaaa

{tab}measure 'Total Atendimentos' = COUNTROWS(Atendimentos)
{tab}{tab}formatString: 0

{tab}measure 'Atendimentos YoY %' = ```
{tab}{tab}{tab}VAR atual = [Total Atendimentos]
{tab}{tab}{tab}VAR anterior = CALCULATE([Total Atendimentos], SAMEPERIODLASTYEAR(Calendario[Data]))
{tab}{tab}{tab}RETURN DIVIDE(atual - anterior, anterior)
{tab}{tab}{tab}```

{tab}measure 'Tempo Médio Permanência' = AVERAGEX(FILTER(Atendimentos, Atendimentos[tp_atendimento] = "I"), Atendimentos[dias_internacao])

{tab}column id_atendimento
{tab}{tab}dataType: int64
{tab}{tab}sourceColumn: id_atendimento

{tab}column dias_internacao = DATEDIFF(Atendimentos[dt_atendimento], Atendimentos[dt_alta], DAY)
{tab}{tab}dataType: int64

{tab}partition Atendimentos = m
{tab}{tab}mode: import
{tab}{tab}source =
{tab}{tab}{tab}{tab}let
{tab}{tab}{tab}{tab}    Source = GoogleBigQuery.Database([BillingProject="rx-bi-prd"]),
{tab}{tab}{tab}{tab}    #"rx-dados-prd" = Source{{[Name="rx-dados-prd"]}}[Data],
{tab}{tab}{tab}{tab}    dw_Schema = #"rx-dados-prd"{{[Name="dw_assistencial",Kind="Schema"]}}[Data],
{tab}{tab}{tab}{tab}    tbl = dw_Schema{{[Name="assistencial_fato_atendimento",Kind="Table"]}}[Data],
{tab}{tab}{tab}{tab}    #"Linhas Filtradas" = Table.SelectRows(tbl, each [dt_atendimento] >= #date(2023, 1, 1)),
{tab}{tab}{tab}{tab}    #"Mesclado" = Table.NestedJoin(#"Linhas Filtradas", {{"id_unidade"}}, Unidades, {{"id_unidade"}}, "U", JoinKind.LeftOuter),
{tab}{tab}{tab}{tab}    #"Expandido" = Table.ExpandTableColumn(#"Mesclado", "U", {{"nm_unidade"}}),
{tab}{tab}{tab}{tab}    #"Agrupado" = Table.Group(#"Expandido", {{"id_unidade"}}, {{{{"qt", each Table.RowCount(_), Int64.Type}}}})
{tab}{tab}{tab}{tab}in
{tab}{tab}{tab}{tab}    #"Expandido"
""", encoding="utf-8")
    (sm / "tables" / "Metas.tmdl").write_text(f"""table Metas
{tab}column meta
{tab}{tab}dataType: double

{tab}partition Metas = m
{tab}{tab}mode: import
{tab}{tab}source =
{tab}{tab}{tab}{tab}let
{tab}{tab}{tab}{tab}    Source = SharePoint.Files("https://redeexemplo.sharepoint.com/sites/bi", [ApiVersion = 15]),
{tab}{tab}{tab}{tab}    Arquivo = Excel.Workbook(Source{{0}}[Content]),
{tab}{tab}{tab}{tab}    Planilha = Arquivo{{[Item="Metas",Kind="Sheet"]}}[Data]
{tab}{tab}{tab}{tab}in
{tab}{tab}{tab}{tab}    Planilha
""", encoding="utf-8")
    (sm / "relationships.tmdl").write_text(f"""relationship 5b2c-1
{tab}fromColumn: Atendimentos.id_unidade
{tab}toColumn: Metas.id_unidade
{tab}crossFilteringBehavior: bothDirections
""", encoding="utf-8")
    (sm / "roles").mkdir(exist_ok=True)
    (sm / "roles" / "Unidade.tmdl").write_text(f"""role Unidade
{tab}modelPermission: read

{tab}tablePermission Atendimentos = [id_unidade] IN LOOKUPVALUE(Acessos[id_unidade], Acessos[email], USERPRINCIPALNAME())
""", encoding="utf-8")
    rep = root / "Painel Assistencial.Report" / "definition" / "pages"
    for pg in range(4):
        (rep / f"p{pg}" / "visuals" / "v1").mkdir(parents=True, exist_ok=True)
        (rep / f"p{pg}" / "page.json").write_text("{}", encoding="utf-8")
        for v in range(R.randint(3, 9)):
            (rep / f"p{pg}" / "visuals" / f"v{v}").mkdir(parents=True, exist_ok=True)
            (rep / f"p{pg}" / "visuals" / f"v{v}" / "visual.json").write_text("{}", encoding="utf-8")
    # PBIP 2: model.bim (TMSL) com SQL nativo
    sm2 = root / "Indicadores Diretoria.SemanticModel"
    sm2.mkdir(parents=True, exist_ok=True)
    bim = {"model": {"tables": [
        {"name": "Receita", "columns": [{"name": "valor", "dataType": "decimal"}, {"name": "margem", "type": "calculated", "expression": "[valor]*0.2"}],
         "measures": [{"name": "Receita Total", "expression": "SUM(Receita[valor])"},
                      {"name": "Receita YTD", "expression": "TOTALYTD([Receita Total], Calendario[Data])"},
                      {"name": "Ranking Unidade", "expression": "RANKX(ALL(Unidade), CALCULATE(SUMX(Receita, Receita[valor] * Receita[qt])))"}],
         "partitions": [{"name": "p", "mode": "directQuery", "source": {"type": "m", "expression": [
             "let", '    Source = GoogleBigQuery.Database([BillingProject="rx-bi-prd"]),',
             '    q = Value.NativeQuery(Source{[Name="rx-bi-prd"]}[Data], "SELECT u.nm_unidade, SUM(f.vl_total) AS valor FROM `rx-dados-prd.dw_faturamento.faturamento_faturamento` f JOIN `rx-dados-prd.dw_faturamento.faturamento_unidade` u ON f.id_unidade = u.id_unidade OR f.id_unidade_origem = u.id_unidade GROUP BY 1", null, [EnableFolding=true])',
             "in", "    q"]}}]},
        {"name": "Calendario", "partitions": [{"name": "c", "mode": "import", "source": {"type": "calculated", "expression": "CALENDARAUTO()"}}],
         "columns": [{"name": "Data", "dataType": "dateTime"}]}],
        "relationships": [{"name": "r1", "fromTable": "Receita", "fromColumn": "data", "toTable": "Calendario", "toColumn": "Data"}],
        "roles": [], "expressions": []}}
    (sm2 / "model.bim").write_text(json.dumps(bim, ensure_ascii=False), encoding="utf-8")
    # Scanner JSON (exportado) com 3 modelos e relatórios
    def m_bq(proj, ds, tbl):
        return (f'let\n    Source = GoogleBigQuery.Database(),\n    #"{proj}" = Source{{[Name="{proj}"]}}[Data],\n'
                f'    s = #"{proj}"{{[Name="{ds}",Kind="Schema"]}}[Data],\n    t = s{{[Name="{tbl}",Kind="Table"]}}[Data]\nin\n    t')

    mart = [r for r in refs if ".mart_bi." in r or ".semantic_bi." in r or ".dw_faturamento." in r][:12] or refs[:12]
    datasets = []
    for i, (name, n_tab) in enumerate((("Faturamento Hospitalar", 6), ("Ocupação de Leitos", 4), ("Glosas e Convênios", 5))):
        tables = []
        for t in range(n_tab):
            proj, ds, tbl = mart[(i * 3 + t) % len(mart)].split(".")
            tables.append({"name": f"T{t}_{tbl}", "columns": [{"name": "c1", "dataType": "Int64"}] * R.randint(5, 30),
                           "measures": [{"name": f"m{k}", "expression": R.choice(["SUM(T[v])", "CALCULATE(SUM(T[v]), FILTER(ALL(T), T[a] > 0))",
                                                                                  "DIVIDE(SUMX(T, T[v] * T[q]), [Total])", "TOTALYTD(SUM(T[v]), D[Data])"])}
                                        for k in range(R.randint(2, 15))],
                           "source": [{"expression": m_bq(proj, ds, tbl)}]})
        datasets.append({"id": f"ds-{i}", "name": name, "configuredBy": "bi.admin@redeexemplo.com.br", "targetStorageMode": "Abf",
                         "isEffectiveIdentityRequired": i == 0, "tables": tables, "expressions": [], "roles": [],
                         "datasourceUsages": [{"datasourceInstanceId": "dsi-bq"}], "createdDate": "2024-03-01T00:00:00Z"})
    datasets.append({"id": "ds-3", "name": "Painel Assistencial (cópia)", "configuredBy": "analista01@redeexemplo.com.br", "targetStorageMode": "Abf",
                     "tables": [{"name": "Atendimentos", "columns": [], "measures": [], "source": [{"expression": m_bq("rx-dados-prd", "dw_assistencial", "assistencial_fato_atendimento")}]}],
                     "datasourceUsages": [{"datasourceInstanceId": "dsi-bq"}]})
    scan = {"workspaces": [
        {"id": "ws-1", "name": "BI Corporativo", "type": "Workspace", "state": "Active", "isOnDedicatedCapacity": True, "capacityId": "cap-1",
         "datasets": datasets[:3],
         "reports": [{"id": f"rp-{k}", "name": n, "datasetId": f"ds-{k % 3}", "reportType": "PowerBIReport", "createdDateTime": "2024-04-01T00:00:00Z",
                      "modifiedDateTime": "2026-10-01T00:00:00Z"} for k, n in enumerate(["Faturamento Mensal", "Faturamento Mensal - v2", "Ocupação Diária",
                                                                                       "Glosas por Convênio", "Faturamento Mensal (cópia)", "Painel Executivo"])],
         "dashboards": [{"id": "db-1", "displayName": "Diretoria", "tiles": [{"datasetId": "ds-0"}, {"datasetId": "ds-1"}]}],
         "dataflows": [{"objectId": "df-1", "name": "Dataflow Cadastro Convênios", "datasourceUsages": [{"datasourceInstanceId": "dsi-sp"}]}]},
        {"id": "ws-2", "name": "Analistas - Assistencial", "type": "Workspace", "state": "Active", "isOnDedicatedCapacity": False,
         "datasets": datasets[3:], "reports": [{"id": "rp-9", "name": "Painel Assistencial - teste", "datasetId": "ds-3", "reportType": "PowerBIReport"}],
         "dashboards": [], "dataflows": []}],
        "datasourceInstances": [{"datasourceType": "GoogleBigQuery", "connectionDetails": {"server": "bigquery.googleapis.com"}, "datasourceId": "dsi-bq",
                                 "gatewayId": "gw-1"},
                                {"datasourceType": "SharePointList", "connectionDetails": {"url": "https://redeexemplo.sharepoint.com/sites/bi"},
                                 "datasourceId": "dsi-sp", "gatewayId": None}]}
    (root / "scanner_result_export.json").write_text(json.dumps(scan, ensure_ascii=False), encoding="utf-8")
