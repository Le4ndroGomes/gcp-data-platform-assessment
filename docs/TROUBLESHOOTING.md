# Troubleshooting

| Sintoma | Causa provável | Ação |
|---|---|---|
| `zsh: command not found: python` | macOS/Cloud Shell só têm `python3` fora do ambiente virtual | Use `python3 main.py ...` ou ative o venv (`source .venv/bin/activate`) |
| `gcloud ... add-iam-policy-binding` pede para escolher uma condição (lista de 1 a N) | A política IAM do projeto já tem bindings condicionais | Interrompa (Ctrl+C) e repita com `--condition=None`; o script `scripts/setup_assessment_sa.sh` já faz isso |
| `Project ... lacks an 'environment' tag` ao rodar `gcloud config set project` | Política de governança do projeto | É só um aviso; não impede a execução |
| `Reauthentication failed. cannot prompt during non-interactive execution` | Sessão do `gcloud` expirada | `gcloud auth login` (e `gcloud auth application-default login` para a ferramenta) |
| Vários componentes **Permission Denied** com `Request had insufficient authentication scopes` | Token de impersonação emitido só com escopo `cloud-platform.read-only` (versões anteriores a 1.1.0) | Atualize a ferramenta: ela agora usa o escopo `cloud-platform` (o acesso efetivo continua limitado ao papel IAM somente leitura) e reexecute os módulos afetados |
| Preflight sem pendências, mas a coleta mostra **Permission Denied** | O preflight testa permissões IAM, não o escopo do token nem as APIs de cada serviço | Veja `missing_permissions.json`; ele traz a mensagem de erro de cada chamada |
| Preflight ou coleta lentos | Muitas regiões BigQuery, datasets ou buckets | Restrinja com `--regions` ou `--projects`; ajuste `execution.max_in_flight_requests`. Em caso de `429`, reduza-o ou use `--sequential` |
| `ADC indisponível` | Sem Application Default Credentials | `gcloud auth application-default login` ou `GOOGLE_APPLICATION_CREDENTIALS` |
| `Falha ao obter token GCP` com impersonação | Falta `roles/iam.serviceAccountTokenCreator` na SA alvo | Conceder ao principal de origem |
| Status **Not Available** com `SERVICE_DISABLED` | API não habilitada no projeto | Esperado se o serviço não é usado; a ferramenta não habilita APIs |
| Status **Permission Denied** | Papel ausente | Ver `missing_permissions.json` e `permissions_matrix.md`; reexecute só o módulo afetado (`--module`) |
| `quota exceeded` / `USER_PROJECT_DENIED` | Quota project ausente/sem permissão | Defina `gcp.quota_project` e conceda `serviceusage.services.use` |
| `CostLimitExceeded` no log | Dry run acima do limite | Reduza `--days`, aumente `--max-bytes-billed` conscientemente, ou restrinja `--projects/--regions` |
| `Access Denied ... INFORMATION_SCHEMA.JOBS_BY_PROJECT` | Falta `bigquery.jobs.listAll` | `roles/bigquery.resourceViewer` no projeto |
| `Access Denied ... INFORMATION_SCHEMA.TABLES` | Falta `bigquery.tables.list` / `routines.list` | `roles/bigquery.metadataViewer` |
| `Unrecognized name` em uma consulta | Coluna ausente na versão/região | O dry run de schema substitui por NULL; reporte o caso com o `execution.log` |
| Airflow sem DAGs | REST API indisponível ou sem papel no Airflow | `roles/composer.user` + papel Viewer no Airflow; ou `pipelines.airflow_rest_api: false` |
| Power BI `401` / `403` | SP sem "read-only admin APIs" ou com permissões admin-consent | Ver `docs/PERMISSIONS.md` (seção Power BI) |
| Scanner sem tabelas/expressões | Tenant settings de metadata scanning desabilitadas | Habilitar "detailed metadata" e "DAX and mashup expressions" |
| `PyYAML não instalado` | Dependência ausente | `pip install -r requirements.txt` (ou use config `.json`) |
| PyPI bloqueado no ambiente | Proxy corporativo | `pip download` em outra máquina e `pip install --no-index --find-links wheels/ -r requirements.txt` |
| Execução interrompida | Rede / timeout | Reexecute o mesmo `--output`: módulos são independentes e `--analyze-only` regenera o relatório |

Diagnóstico: `execution.log` (DEBUG completo, com segredos mascarados), `raw/_bigquery_query_log.json`
(cada consulta: estimativa, bytes faturados, duração, status) e `collection_manifest.json` (hash SHA-256 de cada arquivo).
