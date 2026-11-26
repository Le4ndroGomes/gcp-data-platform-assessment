# APIs utilizadas

Todas as chamadas são de leitura. O cliente HTTP (`utils/http.py`) bloqueia `PUT/PATCH/DELETE` e só permite `POST`
em endpoints de leitura listados em `READ_ONLY_POST_ALLOWLIST`. Nenhum dado coletado é enviado a serviços além das
próprias APIs Google/Microsoft consultadas.

| Módulo | API / endpoint | Uso | Documentação |
|---|---|---|---|
| discovery | Cloud Resource Manager v3 `projects:search`, `projects.get`, `folders:search`, `organizations:search` | Organização, folders, projetos, labels | https://cloud.google.com/resource-manager/reference/rest |
| discovery | Service Usage v1 `services.list?filter=state:ENABLED` | Serviços habilitados | https://cloud.google.com/service-usage/docs/reference/rest |
| discovery | Cloud Asset Inventory v1 `searchAllResources` | Recursos provisionados por tipo/localização | https://cloud.google.com/asset-inventory/docs/reference/rest |
| discovery | Cloud Billing v1 `projects.getBillingInfo`, `billingAccounts.list` | Contas de faturamento | https://cloud.google.com/billing/docs/reference/rest |
| discovery | IAM v1 `serviceAccounts.list` | Service accounts | https://cloud.google.com/iam/docs/reference/rest |
| discovery | Compute v1 `networks`, `aggregated/subnetworks`, `aggregated/vpnTunnels`, `aggregated/interconnectAttachments`, `aggregated/routers`, `getXpnHost` | VPC, conectividade, Shared VPC | https://cloud.google.com/compute/docs/reference/rest/v1 |
| discovery | Cloud Monitoring v3 `timeSeries.list` (`serviceruntime.googleapis.com/api/request_count`) | Evidência de uso por API | https://cloud.google.com/monitoring/api/metrics_gcp |
| bigquery | BigQuery v2 `datasets.list`, `jobs.insert` (somente `dryRun`), `jobs.query`, `jobs.getQueryResults` | Localizações, validação de schema e consultas INFORMATION_SCHEMA | https://cloud.google.com/bigquery/docs/reference/rest |
| bigquery | INFORMATION_SCHEMA: SCHEMATA, SCHEMATA_OPTIONS, TABLES, TABLE_OPTIONS, TABLE_STORAGE, COLUMNS, COLUMN_FIELD_PATHS, VIEWS, MATERIALIZED_VIEWS, ROUTINES, ROW_ACCESS_POLICIES, PARTITIONS, JOBS_BY_PROJECT, JOBS_TIMELINE_BY_PROJECT | Inventário, volumetria e workloads | https://cloud.google.com/bigquery/docs/information-schema-intro |
| bigquery | Cloud Monitoring `bigquery.googleapis.com/storage/stored_bytes`, `uploaded_bytes` | Crescimento e ingestão via streaming | https://cloud.google.com/monitoring/api/metrics_gcp#gcp-bigquery |
| bigquery_capacity | BigQuery Reservation v1 `reservations`, `capacityCommitments`, `reservations/-/assignments`, `biReservation` | Editions, reservas, commitments, BI Engine | https://cloud.google.com/bigquery/docs/reference/reservations/rest |
| storage | Cloud Storage JSON v1 `buckets.list`, `buckets.getIamPolicy`, `objects.list` (amostra, somente nomes/tamanhos) | Inventário, layout e formatos | https://cloud.google.com/storage/docs/json_api |
| storage | Cloud Monitoring `storage.googleapis.com/storage/total_bytes`, `object_count` | Volume sem listar objetos | https://cloud.google.com/monitoring/api/metrics_gcp#gcp-storage |
| pipelines | BigQuery Data Transfer v1 `transferConfigs`, `runs` | Scheduled queries e transfers | https://cloud.google.com/bigquery/docs/reference/datatransfer/rest |
| pipelines | Composer v1 `environments` + REST API do Airflow 2 (`/api/v1/dags`, `dagRuns`, `tasks`) | DAGs, execuções, operadores | https://cloud.google.com/composer/docs/composer-2/access-airflow-api |
| pipelines | Dataflow v1b3 `jobs:aggregated`, `jobs.get(view=JOB_VIEW_ALL, fields=jobMetadata)` | Jobs e E/S detectadas | https://cloud.google.com/dataflow/docs/reference/rest |
| pipelines | Dataproc v1 `clusters`, `jobs`, `batches` | Clusters, jobs e serverless | https://cloud.google.com/dataproc/docs/reference/rest |
| pipelines | Dataform v1beta1 `repositories`, `workflowConfigs`, `workflowInvocations`, `compilationResults:query` | Grafo de dependências SQLX | https://cloud.google.com/dataform/reference/rest |
| pipelines | Data Fusion v1 `instances` + CDAP `/v3/namespaces/*/apps` | Instâncias e pipelines | https://cloud.google.com/data-fusion/docs/reference/rest |
| pipelines | Pub/Sub v1 `topics`, `subscriptions` | Streaming e subscriptions BigQuery/GCS | https://cloud.google.com/pubsub/docs/reference/rest |
| pipelines | Datastream v1 `streams`, `connectionProfiles` | CDC e origens | https://cloud.google.com/datastream/docs/reference/rest |
| pipelines | Cloud Scheduler v1, Workflows v1 / Workflow Executions v1, Cloud Functions v2, Cloud Run v2 | Orquestração e gatilhos | (documentação de cada serviço) |
| pipelines | BigQuery Connection v1 `connections` | Fontes federadas / BigLake | https://cloud.google.com/bigquery/docs/reference/bigqueryconnection/rest |
| services | Cloud SQL Admin v1, AlloyDB v1, Spanner v1, Compute aggregated instances, GKE v1, Vertex AI v1 (regional), Notebooks v2, Dataplex v1, Looker v1 | Inventário operacional e de IA | (documentação de cada serviço) |
| security | CRM v3 `getIamPolicy`, IAM `serviceAccounts.keys.list(keyTypes=USER_MANAGED)`, BigQuery `datasets.get`, Org Policy v2 `getEffectivePolicy`, Access Context Manager v1, Data Catalog v1 `taxonomies`, BigQuery Data Policy v1, Sensitive Data Protection v2 `tableDataProfiles` | Metadados de segurança | (documentação de cada serviço) |
| billing | Tabela de Billing Export existente (BigQuery) | Custos faturados | https://cloud.google.com/billing/docs/how-to/export-data-bigquery-tables |
| lineage | Data Lineage v1 `searchLinks` | Lineage registrado pelo Dataplex | https://cloud.google.com/dataplex/docs/reference/data-lineage/rest |
| powerbi | `admin/workspaces/modified`, `admin/workspaces/getInfo` → `scanStatus` → `scanResult` | Scanner (metadados, DAX, M, lineage) | https://learn.microsoft.com/rest/api/power-bi/admin/workspace-info-post-workspace-info |
| powerbi | `admin/capacities`, `admin/capacities/refreshables`, `groups/{id}/datasets/{id}/refreshes`, `refreshSchedule`, `admin/activityevents`, `gateways` | Capacidades, refresh, atividade | https://learn.microsoft.com/rest/api/power-bi/ |

## Limites e boas práticas aplicados
- Paginação em todas as listagens (pageToken, OData `@odata.nextLink`, `continuationUri`).
- Retries com backoff exponencial + jitter para 408/429/5xx e `rateLimitExceeded`; respeito a `Retry-After`.
- Concorrência limitada (`execution.concurrency`, `max_in_flight_requests`); Scanner Power BI em lotes de 100 workspaces
  (limites documentados: 500 req/h, 16 simultâneas).
- BigQuery: dry run antes de cada consulta, `maximumBytesBilled` e orçamento total; somente `SELECT`/`WITH`.
