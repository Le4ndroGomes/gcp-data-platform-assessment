# Changelog

## 1.1.0

Correções e documentação a partir da primeira execução real (projeto GCP movimentado, 23 localizações BigQuery,
impersonação de service account, 90 dias de histórico).

### Corrigido
- **Escopo do token na impersonação.** O escopo `cloud-platform.read-only` fazia IAM, Cloud Asset, Pub/Sub, Compute, Monitoring e outras APIs
  retornarem 403 `insufficient authentication scopes` (33 de 74 componentes ficaram sem visibilidade). Agora usa `cloud-platform`; o acesso
  efetivo continua limitado ao papel IAM de leitura e o cliente HTTP segue bloqueando qualquer escrita.
- **Colunas sensíveis por nome:** a regex tinha mais de um grupo de captura (erro do BigQuery em várias regiões) e usava o 3º argumento de
  `REGEXP_EXTRACT` como grupo, não como posição.
- **Fingerprints:** arrays de tabelas referenciadas e de labels podiam conter NULL (`Array cannot have a null element`).
- **Dataplex:** o fallback de regiões usava multi-regiões do BigQuery (`us`, `eu`), rejeitadas pela API (`Malformed name`).
- **Papel customizado:** faltavam `bigquery.bireservations.get` (BI Engine) e `dataform.compilationResults.query` (grafo do Dataform).
- **Cloud Run:** a API v2 rejeita o curinga `-` em `locations`; agora lista por região.
- **Relatório:** zeros enganosos. Cloud Storage, service accounts, billing accounts, rede, policy tags, DLP, VPC-SC e chaves de SA agora aparecem
  como `n/d (sem visibilidade)` quando o componente não foi coletado.
- **Sizing:** removida a heurística "clusters SQL = concorrência / 10", que não é confiável.

### Desempenho
- Módulos independentes rodam em paralelo respeitando dependências (`core/scheduler.py`; `--sequential` ou `execution.parallel_modules: false` desativam).
- Paralelismo dentro dos módulos: tecnologias de pipelines, serviços, verificações de segurança (ACL por dataset, chaves por service account,
  governança por região), amostragem e IAM por bucket e dry runs do preflight por localização. O teto global continua sendo `max_in_flight_requests` (12).
- Medido em projeto movimentado (23 localizações, 2.260 tabelas, 159 buckets): preflight de 2 a 4 min para 20 s; módulos de API de ~14 min para ~5 min;
  resultados equivalentes aos da execução sequencial.
- Corrigido o contador `data_masking`/`policy_tags`, que acumulava valores por região (superestimava).

### Adicionado
- `scripts/setup_assessment_sa.sh` e `scripts/teardown_assessment_sa.sh`: service account sem chave, papel de menor privilégio (projeto ou organização),
  bindings com `--condition=None`, idempotentes, com `--dry-run`.
- `config/iam/custom_role_project.yaml` e `custom_role_organization.yaml`; `--print-custom-role`.
- Seção "Volume por localização" no relatório (base para escolher a região do Databricks).
- Documentação: `CUSTOMER_GUIDE.md`, `CUSTOMER_QUESTIONNAIRE.md`, `SIZING_AND_MIGRATION_INPUTS.md`; troubleshooting ampliado.
- Testes de regressão para cada correção (`tests/test_sandbox_run_fixes.py`).

## 1.0.0

Versão inicial: discovery somente leitura de GCP, BigQuery e Power BI, relatório Markdown e entregáveis CSV/JSON/GraphML/Mermaid/XLSX.
