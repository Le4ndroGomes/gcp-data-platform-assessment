# Matriz de permissões mínimas

A ferramenta é somente leitura. Nenhuma permissão é concedida e nenhuma API é habilitada automaticamente.

| Módulo | APIs | Papéis predefinidos (alternativa) | Permissões testadas no projeto | Fora do projeto |
|---|---|---|---|---|
| discovery | cloudresourcemanager.googleapis.com, serviceusage.googleapis.com, cloudasset.googleapis.com, cloudbilling.googleapis.com, iam.googleapis.com, compute.googleapis.com, monitoring.googleapis.com | roles/browser<br>roles/cloudasset.viewer<br>roles/serviceusage.serviceUsageViewer<br>roles/iam.securityReviewer<br>roles/compute.networkViewer<br>roles/monitoring.viewer<br>roles/billing.viewer (billing account) | resourcemanager.projects.get, serviceusage.services.list, cloudasset.assets.searchAllResources, iam.serviceAccounts.list, compute.networks.list, compute.subnetworks.list, compute.routers.list, compute.vpnTunnels.list, compute.interconnectAttachments.list, monitoring.timeSeries.list | resourcemanager.folders.list / organizations.get (organização)<br>billing.accounts.list (billing account) |
| bigquery | bigquery.googleapis.com, bigquerydatatransfer.googleapis.com, bigqueryreservation.googleapis.com, bigqueryconnection.googleapis.com, monitoring.googleapis.com | roles/bigquery.metadataViewer<br>roles/bigquery.resourceViewer<br>roles/bigquery.jobUser (projeto de execução) | bigquery.datasets.get, bigquery.tables.list, bigquery.tables.get, bigquery.routines.list, bigquery.routines.get, bigquery.jobs.create, bigquery.jobs.listAll, bigquery.transfers.get, bigquery.reservations.list, bigquery.bireservations.get, bigquery.capacityCommitments.list, bigquery.reservationAssignments.list, bigquery.rowAccessPolicies.list, bigquery.connections.list | — |
| storage | storage.googleapis.com, monitoring.googleapis.com | papel customizado (storage.buckets.list/get/getIamPolicy + storage.objects.list)<br>roles/monitoring.viewer | storage.buckets.list, storage.buckets.get, storage.buckets.getIamPolicy, storage.objects.list | — |
| pipelines | composer.googleapis.com, dataflow.googleapis.com, dataproc.googleapis.com, dataform.googleapis.com, datafusion.googleapis.com, pubsub.googleapis.com, datastream.googleapis.com, cloudscheduler.googleapis.com, workflows.googleapis.com, workflowexecutions.googleapis.com, cloudfunctions.googleapis.com, run.googleapis.com | roles/composer.user (+ papel Viewer no Airflow)<br>roles/dataflow.viewer<br>roles/dataproc.viewer<br>roles/dataform.viewer<br>roles/datafusion.viewer<br>roles/pubsub.viewer<br>roles/datastream.viewer<br>roles/cloudscheduler.viewer<br>roles/workflows.viewer<br>roles/cloudfunctions.viewer<br>roles/run.viewer | composer.environments.list, composer.environments.get, dataflow.jobs.list, dataflow.jobs.get, dataproc.clusters.list, dataproc.jobs.list, dataproc.batches.list, dataform.repositories.list, dataform.workflowInvocations.list, dataform.workflowConfigs.list, dataform.compilationResults.get, datafusion.instances.list, pubsub.topics.list, pubsub.subscriptions.list, datastream.streams.list, datastream.connectionProfiles.list, cloudscheduler.jobs.list, workflows.workflows.list, workflows.executions.list, cloudfunctions.functions.list, run.services.list, run.jobs.list | — |
| services | sqladmin.googleapis.com, alloydb.googleapis.com, spanner.googleapis.com, compute.googleapis.com, container.googleapis.com, aiplatform.googleapis.com, notebooks.googleapis.com, dataplex.googleapis.com, looker.googleapis.com | roles/cloudsql.viewer<br>roles/alloydb.viewer<br>roles/spanner.viewer<br>roles/compute.viewer<br>roles/container.clusterViewer<br>roles/aiplatform.viewer<br>roles/notebooks.viewer<br>roles/dataplex.viewer<br>roles/looker.viewer | cloudsql.instances.list, alloydb.clusters.list, spanner.instances.list, compute.instances.list, container.clusters.list, aiplatform.endpoints.list, aiplatform.models.list, aiplatform.pipelineJobs.list, notebooks.instances.list, dataplex.lakes.list, dataplex.datascans.list, looker.instances.list | — |
| billing | bigquery.googleapis.com, cloudbilling.googleapis.com | roles/bigquery.dataViewer SOMENTE no dataset do Billing Export<br>roles/bigquery.jobUser<br>roles/billing.viewer | bigquery.jobs.create | bigquery.tables.getData no dataset do Billing Export |
| security | cloudresourcemanager.googleapis.com, iam.googleapis.com, orgpolicy.googleapis.com, accesscontextmanager.googleapis.com, datacatalog.googleapis.com, bigquerydatapolicy.googleapis.com, dlp.googleapis.com | roles/iam.securityReviewer<br>roles/orgpolicy.policyViewer<br>roles/accesscontextmanager.policyReader (organização)<br>roles/datacatalog.viewer<br>roles/bigquerydatapolicy.viewer<br>roles/dlp.dataProfilesReader | resourcemanager.projects.getIamPolicy, iam.serviceAccountKeys.list, orgpolicy.policy.get, datacatalog.taxonomies.list, bigquery.dataPolicies.list | accesscontextmanager.policies.list / servicePerimeters.list (organização) |
| lineage | datalineage.googleapis.com | roles/datalineage.viewer | datalineage.locations.searchLinks | — |

## Papel customizado recomendado (menor privilégio)

Escopo de **projeto** (padrão; é o que o script `scripts/setup_assessment_sa.sh` cria). Para escopo de organização use `python3 main.py --print-custom-role organization`.

```yaml
title: GCP Data Platform Assessment Reader
description: Leitura de metadados para assessment (sem acesso a dados de negocio)
stage: GA
includedPermissions:
- aiplatform.endpoints.list
- aiplatform.models.list
- aiplatform.pipelineJobs.list
- alloydb.clusters.list
- bigquery.bireservations.get
- bigquery.capacityCommitments.list
- bigquery.connections.list
- bigquery.dataPolicies.list
- bigquery.datasets.get
- bigquery.jobs.create
- bigquery.jobs.listAll
- bigquery.reservationAssignments.list
- bigquery.reservations.list
- bigquery.routines.get
- bigquery.routines.list
- bigquery.rowAccessPolicies.list
- bigquery.tables.get
- bigquery.tables.list
- bigquery.transfers.get
- cloudasset.assets.searchAllIamPolicies
- cloudasset.assets.searchAllResources
- cloudfunctions.functions.list
- cloudscheduler.jobs.list
- cloudsql.instances.list
- composer.environments.get
- composer.environments.list
- compute.instances.list
- compute.interconnectAttachments.list
- compute.networks.list
- compute.routers.list
- compute.subnetworks.list
- compute.vpnTunnels.list
- container.clusters.list
- datacatalog.taxonomies.list
- dataflow.jobs.get
- dataflow.jobs.list
- dataform.compilationResults.get
- dataform.repositories.list
- dataform.workflowConfigs.list
- dataform.workflowInvocations.list
- datafusion.instances.list
- datalineage.locations.searchLinks
- dataplex.datascans.list
- dataplex.lakes.list
- dataproc.batches.list
- dataproc.clusters.list
- dataproc.jobs.list
- datastream.connectionProfiles.list
- datastream.streams.list
- iam.serviceAccountKeys.list
- iam.serviceAccounts.list
- looker.instances.list
- monitoring.timeSeries.list
- notebooks.instances.list
- orgpolicy.policy.get
- pubsub.subscriptions.list
- pubsub.topics.list
- resourcemanager.projects.get
- resourcemanager.projects.getIamPolicy
- run.jobs.list
- run.services.list
- serviceusage.services.list
- spanner.instances.list
- storage.buckets.get
- storage.buckets.getIamPolicy
- storage.buckets.list
- storage.objects.list
- workflows.executions.list
- workflows.workflows.list
```

## Comandos para o administrador (revisar antes de executar)

Forma recomendada: o script cria a service account, o papel customizado e os bindings, mostra cada comando e pede confirmação.

```bash
./scripts/setup_assessment_sa.sh --project PROJECT_ID --executor user:PESSOA@EMPRESA.COM          # escopo de projeto
./scripts/setup_assessment_sa.sh --project PROJECT_ID --org ORG_ID --executor group:TIME@EMPRESA.COM  # escopo de organização
```

Equivalente manual (escopo de organização). Em projetos com bindings condicionais, use sempre `--condition=None`:

```bash
gcloud iam roles create gcpDataAssessmentReader --organization=ORG_ID --file=config/iam/custom_role_organization.yaml
gcloud organizations add-iam-policy-binding ORG_ID --member=serviceAccount:SA_EMAIL \
  --role=organizations/ORG_ID/roles/gcpDataAssessmentReader --condition=None
# Projeto onde os jobs de metadados (INFORMATION_SCHEMA) serão executados:
gcloud projects add-iam-policy-binding PROJECT_ID --member=serviceAccount:SA_EMAIL --role=roles/bigquery.jobUser --condition=None
# Billing Export (somente o dataset da exportação):
bq add-iam-policy-binding --member=serviceAccount:SA_EMAIL --role=roles/bigquery.dataViewer BILLING_PROJECT:BILLING_DATASET
# Organização (VPC-SC / Org Policy / folders):
gcloud organizations add-iam-policy-binding ORG_ID --member=serviceAccount:SA_EMAIL --role=roles/accesscontextmanager.policyReader --condition=None
gcloud organizations add-iam-policy-binding ORG_ID --member=serviceAccount:SA_EMAIL --role=roles/orgpolicy.policyViewer --condition=None
gcloud organizations add-iam-policy-binding ORG_ID --member=serviceAccount:SA_EMAIL --role=roles/browser --condition=None
```

## Power BI / Microsoft Entra ID

| Item | Detalhe |
|---|---|
| Registro de aplicativo (service principal) no Microsoft Entra ID | Sem permissões de API Power BI com admin consent (exigência da Admin API para SP) |
| Grupo de segurança contendo o SP | Usado nas configurações de tenant abaixo |
| Tenant setting: Service principals can use read-only admin APIs | Habilitar para o grupo do SP (Admin portal > Admin API settings) |
| Tenant setting: Enhance admin APIs responses with detailed metadata | Necessário para datasetSchema (tabelas/colunas/medidas) |
| Tenant setting: Enhance admin APIs responses with DAX and mashup expressions | Necessário para datasetExpressions (DAX/M) |
| Alternativa delegada | Usuário Fabric Administrator com escopo Tenant.Read.All (device code) |
| Histórico de refresh por dataset | SP como membro (Viewer ou superior) dos workspaces avaliados; ou usar admin/capacities/refreshables (capacidades dedicadas) |
| Activity log | admin/activityevents (mesma permissão de read-only admin APIs); 1 dia UTC por chamada, ~30 dias de retenção |
