# GCP Data Platform Assessment — Discovery & Migration Readiness

> **Aviso:** ferramenta da comunidade, mantida por conta própria. **Não é um produto oficial do Databricks** nem tem suporte do Databricks. Fornecida "como está", sem garantias. A coleta é somente leitura, mas revise as permissões e o custo estimado (`--preflight`) antes de executar em produção.

Ferramenta CLI em Python para o discovery **somente leitura** de plataformas de dados em Google Cloud (BigQuery e
demais serviços) e Microsoft Power BI. Ela gera um relatório Markdown único (`assessment_report.md`) e um conjunto de
CSV/JSON/GraphML/Mermaid/XLSX com insumos para sizing e planejamento de migração para Databricks.

O cliente pode executar a ferramenta no próprio ambiente (Cloud Shell, VM ou estação local), sem compartilhar
credenciais com terceiros.

## Garantias operacionais

- **Somente leitura.** O cliente HTTP bloqueia `PUT/PATCH/DELETE` e só aceita `POST` em endpoints de leitura
  (consultas `SELECT`, dry run, `testIamPermissions`, `getIamPolicy`, `searchLinks`, Scanner do Power BI).
- **Nenhuma tabela de negócio é lida.** Volumetria vem de `TABLE_STORAGE`, nunca de `COUNT(*)`.
- **Não cria nem altera recursos.** Não concede permissões, não habilita APIs, não cria tabelas, buckets ou exportações.
- **Custo controlado.** Cada consulta passa por dry run e roda com `maximumBytesBilled`, dentro de um orçamento total.
  O preflight estima o custo da coleta antes de executar.
- **Privacidade.** Principals são pseudonimizados por padrão (HMAC com salt local). Segredos, variáveis de ambiente e
  connection strings nunca são persistidos. O texto SQL só é lido com `--include-sql` e só é gravado com `--store-sql`.
- **Nada sai do ambiente.** Não há telemetria; o relatório não usa recursos externos.
- **Resiliente.** Paginação, retries com backoff exponencial, timeouts e rate limits. A falta de permissão em um
  módulo não interrompe os demais e fica registrada em `assessment_coverage.json` / `missing_permissions.json`.

## Começando (cliente)

1. O administrador do GCP cria a service account de leitura com `./scripts/setup_assessment_sa.sh` (papel de menor privilégio, sem chave).
2. Quem executa instala, roda o preflight e depois o assessment.
3. Revisa a pasta de resultado e a devolve, junto com o [questionário complementar](docs/CUSTOMER_QUESTIONNAIRE.md).

Passo a passo completo: [`docs/CUSTOMER_GUIDE.md`](docs/CUSTOMER_GUIDE.md). O que o relatório entrega para sizing e planejamento, e o que falta:
[`docs/SIZING_AND_MIGRATION_INPUTS.md`](docs/SIZING_AND_MIGRATION_INPUTS.md).

## Instalação

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # Python 3.10+
```

Fora do ambiente virtual, use sempre `python3` (em macOS e no Cloud Shell o comando `python` pode não existir).

`msal` (Power BI online), `sqlglot` (análise de SQL) e `openpyxl` (XLSX) são opcionais: sem eles, a ferramenta
degrada graciosamente (modo offline do Power BI, análise SQL por regex, sem XLSX).

Ambiente sem acesso ao PyPI: em outra máquina, rode `pip download -r requirements.txt -d wheels/`; no ambiente do
cliente, rode `pip install --no-index --find-links wheels/ -r requirements.txt`.

## Autenticação

**GCP:** Application Default Credentials.

```bash
gcloud auth application-default login                 # usuário
# ou: export GOOGLE_APPLICATION_CREDENTIALS=sa.json     # service account
# ou impersonação (recomendado):
python3 main.py --all --impersonate-service-account assessment-reader@PROJ.iam.gserviceaccount.com
```

**Power BI (Microsoft Entra ID, independente do GCP):**

```bash
export PBI_TENANT_ID=... PBI_CLIENT_ID=... PBI_CLIENT_SECRET=...   # service principal
# ou powerbi.auth_mode: device_code (Fabric Administrator)
```

Sem credenciais Entra, use o modo offline: `--powerbi-offline ./artefatos` com PBIP/TMDL, PBIT, `model.bim` ou JSON
exportado do Scanner.

## Fluxo recomendado

```bash
# 1. Validar autenticação, APIs, permissões e estimar o custo (gera preflight.md e permissions_matrix.md)
python3 main.py --preflight --config config.yaml

# 2. Assessment completo
python3 main.py --all --days 90 --config config.yaml

# 3. Reprocessar o relatório offline (sem acessar o GCP)
python3 main.py --analyze-only --input ./output
```

### Outros comandos

```bash
python3 main.py --module discovery
python3 main.py --module bigquery --days 90
python3 main.py --module powerbi                       # online (Entra) e/ou --powerbi-offline PASTA
python3 main.py --module billing
python3 main.py --projects project-a,project-b --all
python3 main.py --focus-file scope.example.yaml --days 90   # assessment por caso de uso
python3 main.py --demo --output ./examples/demo_output      # massa sintética + relatório de exemplo
python3 main.py --print-queries                             # docs/SQL_QUERIES.md com todas as consultas
python3 main.py --print-custom-role project                 # YAML do papel IAM de menor privilégio (project | organization)
```

| Parâmetro | Descrição |
|---|---|
| `--module` | `discovery`, `bigquery`, `storage`, `pipelines`, `services`, `security`, `billing`, `lineage`, `powerbi` (repetível ou separado por vírgula) |
| `--projects` / `--exclude-projects` / `--regions` / `--org` | Escopo |
| `--days` | Janela de workloads (7 a 180) |
| `--include-sql` / `--store-sql` | Autoriza ler definições SQL/DDL (análise SQLGlot) / também gravá-las |
| `--no-anonymize` | Desativa a pseudonimização (use apenas com autorização) |
| `--max-bytes-billed` / `--max-total-bytes-billed` | Limites de custo por consulta e total |
| `--timeout` / `--concurrency` | Timeout HTTP e paralelismo por módulo |
| `--sequential` | Executa os módulos um por vez (padrão: módulos independentes em paralelo, respeitando dependências) |
| `--powerbi-offline` | Pasta de artefatos Power BI |
| `--focus-file` | YAML com relatório, modelo, dataset ou tabelas-semente |

**Desempenho.** Módulos independentes rodam ao mesmo tempo (`execution.parallel_modules`), serviços e recursos dentro de cada módulo também, e o teto
de requisições simultâneas é global (`execution.max_in_flight_requests`, padrão 12). Em um projeto movimentado, o preflight caiu de 2 a 4 min para ~20 s e a
coleta dos módulos de API de ~14 min para ~5 min. Use `--sequential` para depurar.

Execuções incrementais no mesmo `--output` são suportadas: cada módulo pode ser reexecutado isoladamente (por
exemplo, depois de uma permissão concedida) e o relatório é regenerado com todos os dados.

## Módulos

| Módulo | O que coleta |
|---|---|
| discovery | Organização, folders, projetos e ambientes, serviços habilitados, recursos (Asset Inventory), billing accounts, service accounts, VPC/conectividade, evidência de uso por API |
| bigquery | Datasets, tabelas, views, MVs, externas, snapshots/clones, rotinas, colunas e tipos, particionamento/clustering, opções/expiração, RLS, policy tags, volumetria lógica/física/long-term, crescimento; workloads (JOBS/TIMELINE): volume, latência P50–P99, fila, cache, slots, concorrência, sazonalidade, fingerprints, consumidores, tabelas mais acessadas, evidência de Power BI; Editions, reservas, commitments e BI Engine |
| storage | Buckets, classes, volume e objetos (Monitoring), lifecycle, versionamento, retenção, CMEK, acesso público, amostra de formatos e small files |
| pipelines | Scheduled queries/DTS, Composer/Airflow, Dataflow, Dataproc, Dataform (grafo), Data Fusion, Pub/Sub, Datastream, Scheduler, Workflows, Functions, Cloud Run, conexões federadas |
| services | Cloud SQL (prontidão CDC), AlloyDB, Spanner, Compute, GKE, Vertex AI, Dataplex, Looker |
| security | IAM, chaves de SA, ACL/CMEK de datasets, VPC-SC, Org Policies, policy tags, data masking, perfis DLP, indícios LGPD por nome de coluna |
| billing | Billing Export: 3/6/12 meses, por projeto/serviço/SKU, categorias, créditos, BigQuery on-demand × Editions × storage, cost drivers |
| lineage | Data Lineage API + grafo combinado (JOBS, Dataform, configurações, Power Query), com status confirmed/inferred/unknown |
| powerbi | Workspaces, relatórios, dashboards, modelos, dataflows, capacidades, gateways, fontes, DAX, M, RLS, modos de armazenamento, refresh e atividade; correlação Power BI × BigQuery com nível de confiança |

## Entregáveis (`output/`)

`assessment_report.md` (relatório principal), `assessment_report.xlsx`, `infrastructure_inventory.csv`,
`bigquery_datasets.csv`, `bigquery_tables.csv`, `bigquery_routines.csv`, `workload_analysis.csv`,
`query_complexity.csv`, `capacity_analysis.csv`, `pipeline_inventory.csv`, `cloud_storage_inventory.csv`,
`powerbi_inventory.csv`, `powerbi_dependencies.csv`, `powerbi_refresh_history.csv`, `cost_analysis.csv`,
`security_findings.csv`, `data_lineage.json`, `data_lineage.graphml`, `data_lineage.mmd`, `architecture_as_is.mmd`,
`migration_readiness.json`, `databricks_sizing_inputs.json`, `poc_candidates.json`, `assessment_coverage.json`,
`missing_permissions.json`, `permissions_matrix.md`, `collection_manifest.json` (SHA-256 de cada arquivo),
`execution.log` e `raw/` (dados brutos agregados, usados pelo `--analyze-only`).

Seções do relatório: Executive Summary, Architecture AS-IS, Platform Footprint, Data Volume, Workload Distribution,
Pipeline Complexity, Power BI Footprint, Cost Breakdown, Security & Governance, Migration Readiness, Sizing Inputs,
Risks & Recommendations, Assessment Coverage e apêndices de metodologia.

Exemplo completo gerado com dados sintéticos: [`examples/demo_output/assessment_report.md`](examples/demo_output/assessment_report.md) (a pasta `raw/` não é versionada; regenere com `python3 main.py --demo`). Artefatos Power BI de exemplo para o modo offline: [`examples/powerbi_offline_sample/`](examples/powerbi_offline_sample/).

## Metodologias (resumo)

- **Cobertura:** cada componente tem status `Complete`, `Partial`, `Permission Denied`, `Not Available`, `Not
  Applicable` ou `Not Assessed`. O relatório diferencia ausência de recursos (coleta completa, nada encontrado) de
  ausência de visibilidade.
- **Complexidade SQL:** features por regex (no modo `features`, calculadas no próprio BigQuery) mais AST SQLGlot (no
  modo `full`). Os critérios estão no apêndice A.1 do relatório. Anti-patterns aparecem como estáticos (potenciais) ou
  confirmados por execução.
- **Migration Readiness Score (0–100):** seis dimensões com pesos configuráveis. Dimensões sem evidência são
  excluídas e os pesos são renormalizados; a cobertura de evidências é reportada.
- **Sizing:** insumos observados e cenários conservador, esperado e pico, sem converter slots em DBUs, com a lista
  de benchmarks para a POC.
- **Caso de uso:** percurso upstream/downstream no grafo, com métricas e percentuais com denominador explícito.
  Dependências compartilhadas contam uma vez.

## Documentação

- [`docs/CUSTOMER_GUIDE.md`](docs/CUSTOMER_GUIDE.md): passo a passo de execução para o cliente
- [`docs/CUSTOMER_QUESTIONNAIRE.md`](docs/CUSTOMER_QUESTIONNAIRE.md): informações que a ferramenta não coleta (SLAs, donos, residência, prazo)
- [`docs/SIZING_AND_MIGRATION_INPUTS.md`](docs/SIZING_AND_MIGRATION_INPUTS.md): o que usar do relatório para sizing e plano de migração, e as lacunas
- [`docs/PERMISSIONS.md`](docs/PERMISSIONS.md): matriz IAM por módulo, papel customizado, comandos `gcloud`/`bq` e requisitos Entra ID
- [`docs/APIS.md`](docs/APIS.md): APIs e endpoints utilizados
- [`docs/SQL_QUERIES.md`](docs/SQL_QUERIES.md): todas as consultas SQL (gerado automaticamente)
- [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md): limitações conhecidas
- [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md): diagnóstico de problemas

## Testes

```bash
pip install -r requirements-dev.txt
python -m pytest -q tests
```

Os testes usam APIs simuladas: paginação, retry/429, classificação de erros, bloqueio de escrita, controle de custo,
decodificação de linhas BigQuery, parsers TMDL/PBIT/M/DAX, complexidade SQL, grafo de lineage, cobertura,
readiness, workloads, volumetria sem dupla contagem e o fluxo completo da demo.

## Estrutura

```text
main.py                 CLI
scripts/                setup_assessment_sa.sh e teardown_assessment_sa.sh (service account e papel de menor privilégio)
config.yaml             configuração de exemplo
config/                 snapshot de disponibilidade regional do Databricks on GCP; config/iam/ = papéis customizados (YAML)
core/                   contexto, cobertura, orquestração da análise
collectors/             discovery, bigquery_metadata, bigquery_workloads, bigquery_capacity, cloud_storage,
                        pipelines, services, powerbi (+ parsers offline), billing, security, lineage
analyzers/              inventory, workload_analysis, volume_analysis, sql_complexity, code_and_pipelines,
                        cost_analysis, security_findings, platform_footprint, migration_readiness, sizing, use_case
exporters/              csv_exporter, json_exporter, excel_exporter, markdown_report
utils/                  authentication, http (somente leitura), bq (dry run/custo), permissions, redaction,
                        schedule, logging, fmt
demo/                   massa sintética e catálogo de consultas
tests/                  testes unitários com mocks
examples/               relatório de exemplo e artefatos Power BI offline de exemplo
```

Para adicionar um coletor, crie `collectors/novo.py` com `collect(ctx)`. Use `ctx.gcp` (cliente somente leitura),
`ctx.coverage.ok/fail/finalize` e `ctx.save_raw`, registre o módulo em `main.py` (`plan`, `COVERAGE_MODULES`) e as
permissões em `utils/permissions.py` (`MATRIX`).
