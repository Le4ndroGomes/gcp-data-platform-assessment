# Guia de execução (para o cliente)

Passo a passo para executar o assessment no ambiente do cliente. A ferramenta é **somente leitura**: coleta metadados, histórico de jobs e configurações do BigQuery e dos demais serviços de dados do GCP. Não lê dados de negócio, não cria recursos e não envia nada para fora do ambiente. O resultado é uma pasta local, que o cliente revisa antes de compartilhar.

Os valores entre `<...>` são **placeholders**: troque pelos do seu ambiente.

| Placeholder | O que é |
|---|---|
| `<ID_DO_PROJETO>` | Projeto que hospeda a service account e onde as consultas de metadados rodam |
| `<EMAIL_DE_QUEM_EXECUTA>` | Pessoa que vai executar a ferramenta |
| `<URL_DO_REPOSITORIO>` | Endereço do repositório recebido (ou use o arquivo `.tgz`) |
| `<PROJETO_BILLING>:<DATASET_BILLING>` | Dataset da exportação de billing (opcional) |

| Parte | Quem | Tempo |
|---|---|---|
| A. Criar a service account de leitura | Administrador do GCP (IAM) | 10 min |
| B a F. Instalar, rodar e devolver | Quem vai executar | 15 a 60 min |
| G. Remover o acesso ao final | Administrador do GCP | 5 min |

Requisitos: Python 3.10 ou superior e Google Cloud CLI (`gcloud`). O **Cloud Shell** já traz os dois.

---

## Parte A. Criar a service account (administrador do GCP)

A ferramenta usa uma service account dedicada, assumida por **impersonação**. Nenhuma chave é criada.

### A1. Obter a ferramenta e autenticar

```bash
git clone <URL_DO_REPOSITORIO>          # ou: tar -xzf gcp-data-platform-assessment-*.tgz
cd gcp-data-platform-assessment
gcloud auth login
```

### A2. Definir as variáveis

```bash
export PROJ="<ID_DO_PROJETO>"
export SA_NAME="assessment-reader"
export SA_EMAIL="${SA_NAME}@${PROJ}.iam.gserviceaccount.com"
export ME="user:<EMAIL_DE_QUEM_EXECUTA>"
gcloud config set project $PROJ
```

O `gcloud` pode avisar que o projeto não tem a tag `environment`. É só um aviso.

### A3. Criar a service account

```bash
gcloud iam service-accounts create $SA_NAME \
  --display-name="GCP Data Platform Assessment (read-only)" \
  --description="Leitura de metadados para assessment; sem acesso a dados de negocio"
```

### A4. Criar o papel de leitura (menor privilégio)

O arquivo `config/iam/custom_role_project.yaml`, já no repositório, lista só permissões de listagem e leitura de metadados.

```bash
gcloud iam roles create gcpDataAssessmentReader --project=$PROJ \
  --file=config/iam/custom_role_project.yaml
```

O `gcloud` pode perguntar `Are you sure you want to make this change? (Y/n)` por causa de uma permissão em estágio TESTING. Responda `y`.

### A5. Conceder os acessos

```bash
gcloud projects add-iam-policy-binding $PROJ \
  --member=serviceAccount:$SA_EMAIL \
  --role=projects/$PROJ/roles/gcpDataAssessmentReader \
  --condition=None --format=none

gcloud projects add-iam-policy-binding $PROJ \
  --member=serviceAccount:$SA_EMAIL \
  --role=roles/bigquery.jobUser \
  --condition=None --format=none
```

- `--condition=None` é necessário quando o projeto já tem bindings condicionais; sem ele o `gcloud` lista as condições e pede um número. Se isso acontecer, interrompa com `Ctrl+C` e repita com o flag.
- `--format=none` evita imprimir a política IAM inteira.
- O `roles/bigquery.jobUser` serve só para executar consultas `SELECT` em `INFORMATION_SCHEMA` (metadados).

### A6. Permitir que quem executa assuma a service account

```bash
gcloud iam service-accounts add-iam-policy-binding $SA_EMAIL \
  --member=$ME \
  --role=roles/iam.serviceAccountTokenCreator
```

O acesso pode levar de 1 a 2 minutos para valer.

### A7. Verificar

```bash
gcloud iam service-accounts describe $SA_EMAIL

gcloud projects get-iam-policy $PROJ \
  --flatten="bindings[].members" \
  --filter="bindings.members:$SA_EMAIL" \
  --format="table(bindings.role)"
```

Esperado: `projects/<ID_DO_PROJETO>/roles/gcpDataAssessmentReader` e `roles/bigquery.jobUser`.

### Opcionais

**Billing Export** (custo faturado; concede leitura somente nesse dataset):

```bash
bq add-iam-policy-binding --member=serviceAccount:$SA_EMAIL \
  --role=roles/bigquery.dataViewer <PROJETO_BILLING>:<DATASET_BILLING>
```

**Mais de um projeto:** o papel customizado existe por projeto. Repita A4 e A5 em cada projeto adicional (trocando `PROJ`), mantendo a mesma service account. Para cobrir uma organização inteira, veja `docs/PERMISSIONS.md`.

**Atalho:** o script `./scripts/setup_assessment_sa.sh --project $PROJ --executor $ME` faz de A3 a A6 em um comando (use `--dry-run` para só imprimir).

---

## Parte B. Instalar (quem executa)

```bash
cd gcp-data-platform-assessment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Fora do ambiente virtual, use sempre `python3`. Sem acesso ao PyPI: em outra máquina, `pip download -r requirements.txt -d wheels/`; no ambiente do cliente, `pip install --no-index --find-links wheels/ -r requirements.txt`.

## Parte C. Autenticar (quem executa)

```bash
gcloud auth application-default login
export GCPA_REDACTION_SALT="$(openssl rand -hex 32)"   # salt local da pseudonimização; guarde, não compartilhe

export PROJ="<ID_DO_PROJETO>"
export SA_EMAIL="assessment-reader@${PROJ}.iam.gserviceaccount.com"
```

Em um terminal novo, repita as variáveis.

## Parte D. Preflight (quem executa)

Não coleta dados. Valida autenticação, APIs e permissões, e **estima o custo** das consultas de metadados do BigQuery.

```bash
python3 main.py --preflight --projects $PROJ --impersonate-service-account $SA_EMAIL
```

Leia `output/preflight.md`:

- **Permissões ausentes:** peça ao administrador para ajustar o papel (Parte A).
- **APIs não habilitadas:** esperado quando o serviço não é usado. A ferramenta não habilita APIs.
- **Custo estimado:** confirme que é aceitável. O limite padrão é de 200 GiB por consulta e 2 TiB no total; acima dele a consulta não roda.

## Parte E. Executar o assessment (quem executa)

```bash
python3 main.py --all --days 90 --projects $PROJ --impersonate-service-account $SA_EMAIL
```

Acompanhe em outro terminal com `tail -f output/execution.log`. Para vários projetos, use `--projects PROJ_A,PROJ_B`.

Se um módulo falhar por permissão, os demais continuam. Depois de corrigir, reexecute só o módulo:

```bash
python3 main.py --module security --projects $PROJ --impersonate-service-account $SA_EMAIL
```

Regenerar o relatório sem acessar o GCP: `python3 main.py --analyze-only --input ./output`.

Para incluir custo faturado e domínios corporativos, copie `config.yaml`, preencha `billing.export_table` e `security.allowed_domains` e acrescente `--config meu-config.yaml` aos comandos.

**Power BI (opcional), sem Entra ID**, a partir de arquivos PBIP, PBIT ou `model.bim`:

```bash
python3 main.py --module powerbi --powerbi-offline <PASTA_COM_ARTEFATOS_POWERBI>
```

## Parte F. Revisar e devolver (quem executa)

1. Abra `output/assessment_report.md` e leia **Assessment Coverage**. Componentes "Permission Denied" ou "Not Assessed" significam **sem visibilidade**, não ausência de recursos.
2. Revise antes de compartilhar: usuários e service accounts são pseudonimizados, mas **nomes de projetos, datasets, tabelas e buckets não são**.
3. Compacte e envie a pasta:

```bash
tar -czf assessment_output.tgz output/
```

**Não envie** o `GCPA_REDACTION_SALT` nem credenciais. Responda também o questionário `docs/CUSTOMER_QUESTIONNAIRE.md` (SLAs, donos dos dados, residência, prazo).

## Parte G. Encerramento (administrador do GCP)

```bash
gcloud projects remove-iam-policy-binding $PROJ --member=serviceAccount:$SA_EMAIL \
  --role=projects/$PROJ/roles/gcpDataAssessmentReader --condition=None --format=none
gcloud projects remove-iam-policy-binding $PROJ --member=serviceAccount:$SA_EMAIL \
  --role=roles/bigquery.jobUser --condition=None --format=none
gcloud iam roles delete gcpDataAssessmentReader --project=$PROJ
gcloud iam service-accounts delete $SA_EMAIL
```

---

## Problemas comuns

| Sintoma | Ação |
|---|---|
| `command not found: python` | Use `python3` ou ative o ambiente virtual (`source .venv/bin/activate`) |
| O `gcloud` lista condições e pede um número | `Ctrl+C` e repita com `--condition=None` |
| `Reauthentication failed` | `gcloud auth login` e `gcloud auth application-default login` |
| Falha ao obter token na impersonação | Aguarde 1 a 2 minutos e confira o `roles/iam.serviceAccountTokenCreator` na service account |
| **Permission Denied** em vários componentes | Veja `output/missing_permissions.json`, ajuste o papel e reexecute só os módulos afetados |
| **Not Available** | API desabilitada no projeto; esperado se o serviço não é usado |
| Execução lenta ou erro 429 | Acrescente `--sequential` ou reduza `execution.max_in_flight_requests` no config |

Mais detalhes no repositório: `docs/CUSTOMER_GUIDE.md`, `docs/PERMISSIONS.md` e `docs/TROUBLESHOOTING.md`.
