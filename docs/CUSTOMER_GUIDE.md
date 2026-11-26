# Guia de execução (para o cliente)

Passo a passo para executar o assessment no seu ambiente. A ferramenta é **somente leitura**: coleta metadados, histórico
de jobs e configurações; não lê dados de negócio, não cria recursos e não altera configurações. Nada é enviado para fora
do seu ambiente: o resultado é uma pasta local que você revisa antes de compartilhar.

## Quem faz o quê

| Etapa | Quem | Tempo estimado |
|---|---|---|
| 1. Criar a service account e conceder acesso | Administrador do GCP (IAM) | 10 min |
| 2. Instalar a ferramenta | Quem vai executar | 5 min |
| 3. Autenticar e configurar | Quem vai executar | 5 min |
| 4. Preflight (valida acesso e estima custo) | Quem vai executar | 1 a 5 min |
| 5. Assessment completo | Quem vai executar | 10 a 40 min (depende do volume de jobs e de regiões) |
| 6. Revisar e devolver a pasta de resultado | Quem vai executar | 15 min |
| Opcional: Power BI | Administrador do Power BI/Fabric | 30 min |

Requisitos: Python 3.10 ou superior e Google Cloud CLI (`gcloud`). O **Cloud Shell** já traz os dois e é a opção mais simples.

## 1. Criar a service account (administrador do GCP)

A ferramenta usa uma service account dedicada, assumida por **impersonação**. Nenhuma chave JSON é criada.

```bash
# Escopo de projeto (recomendado para começar): papel e acessos valem só para o projeto informado
./scripts/setup_assessment_sa.sh --project PROJETO_ID --executor user:pessoa@empresa.com

# Vários projetos
./scripts/setup_assessment_sa.sh --project PROJETO_ID --extra-projects PROJ_B,PROJ_C --executor user:pessoa@empresa.com

# Escopo de organização (inclui folders, VPC Service Controls e Organization Policies)
./scripts/setup_assessment_sa.sh --project PROJETO_ID --org ORG_ID --executor group:time-dados@empresa.com

# Com Billing Export (concede leitura somente nesse dataset)
./scripts/setup_assessment_sa.sh --project PROJETO_ID --executor user:pessoa@empresa.com --billing-dataset PROJ_BILLING:DATASET_BILLING
```

- O script mostra cada comando e pede confirmação. Use `--dry-run` para apenas imprimir e `--yes` para não perguntar.
- É idempotente: reexecutar não duplica nada.
- O papel customizado contém só permissões de listagem e leitura de metadados (`config/iam/custom_role_project.yaml`);
  **não** inclui `bigquery.tables.getData`, `storage.objects.get` nem qualquer permissão de escrita.
  O mesmo YAML pode ser gerado com `python3 main.py --print-custom-role project`.
- `roles/bigquery.jobUser` é necessário para executar consultas `SELECT` em `INFORMATION_SCHEMA` (metadados), não em tabelas de negócio.
- Quem executa precisa de `roles/iam.serviceAccountTokenCreator` na service account; o script concede ao `--executor`.
- A ferramenta **não** concede permissões, **não** habilita APIs e **não** cria recursos. APIs desabilitadas aparecem no
  relatório como "Not Available".
- Para revisar com o time de segurança antes de executar, veja a matriz completa em [`PERMISSIONS.md`](PERMISSIONS.md).

Se o projeto exigir a tag `environment`, o `gcloud` apenas exibe um aviso; ele não impede a execução.

## 2. Instalar

```bash
git clone <URL_DO_REPOSITORIO> gcp-data-platform-assessment && cd gcp-data-platform-assessment
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Sem acesso ao PyPI: em outra máquina, `pip download -r requirements.txt -d wheels/`; neste ambiente,
`pip install --no-index --find-links wheels/ -r requirements.txt`.

## 3. Autenticar e configurar

```bash
gcloud auth application-default login                 # no Cloud Shell já está autenticado
export GCPA_REDACTION_SALT="$(openssl rand -hex 32)"  # salt local da pseudonimização; guarde, não compartilhe
cp config.yaml meu-config.yaml
```

Em `meu-config.yaml`, ajuste no mínimo:

| Campo | Valor |
|---|---|
| `gcp.impersonate_service_account` | `assessment-reader@PROJETO_ID.iam.gserviceaccount.com` |
| `gcp.projects` | Lista de projetos do escopo (vazio = todos os visíveis) |
| `gcp.organization_id` | ID da organização, se concedeu acesso em nível de organização |
| `bigquery.execution_project` | Projeto onde as consultas de metadados rodam |
| `billing.export_table` | Tabela do Billing Export (`projeto.dataset.tabela`), se existir |
| `security.allowed_domains` | Domínios corporativos (e-mail), para classificar acessos externos |

## 4. Preflight

```bash
python3 main.py --preflight --config meu-config.yaml
```

Não coleta dados. Gera `output/preflight.md` com: autenticação, APIs habilitadas, permissões faltantes por módulo e **custo
estimado** das consultas de metadados do BigQuery por projeto e região (dry run). Corrija o que faltar e confirme que o
custo é aceitável. Os limites ficam em `bigquery.max_bytes_billed_per_query` e `bigquery.max_total_bytes_billed`; acima
deles a consulta não roda. Como referência, um projeto movimentado, com 23 localizações e 90 dias de histórico, ficou em cerca de
US$ 2 a US$ 3 de BigQuery on-demand.

## 5. Executar

```bash
python3 main.py --all --days 90 --config meu-config.yaml
```

- Acompanhe com `tail -f output/execution.log`.
- Se um módulo falhar por permissão, os demais continuam. Depois de corrigir, reexecute só o módulo:
  `python3 main.py --module security --config meu-config.yaml`. O relatório é regenerado com tudo.
- Para regenerar o relatório sem acessar o GCP: `python3 main.py --analyze-only --input ./output`.

## 6. Revisar e devolver

1. Abra `output/assessment_report.md` e leia a seção **Assessment Coverage**: ela mostra o que foi coletado e o que ficou sem visibilidade.
   Componentes "Permission Denied" ou "Not Assessed" significam **sem visibilidade**, não ausência de recursos.
2. Revise o conteúdo antes de compartilhar. Principals (usuários e service accounts) são pseudonimizados; **nomes de projetos,
   datasets, tabelas e buckets não são**. Se algum nome for sensível, ajuste antes de enviar.
3. Compacte e envie a pasta de saída:
   ```bash
   tar -czf assessment_output.tgz output/
   ```
   O arquivo `collection_manifest.json` traz o SHA-256 de cada arquivo para conferência de integridade.
4. **Não envie** o `GCPA_REDACTION_SALT` nem credenciais. A ferramenta não grava tokens nem segredos nos arquivos.

## Power BI (opcional)

- **Online:** registrar um app no Microsoft Entra ID, colocá-lo em um grupo de segurança e habilitar no Admin portal, para esse grupo:
  *Service principals can use read-only admin APIs*, *Enhance admin APIs responses with detailed metadata* e
  *Enhance admin APIs responses with DAX and mashup expressions*. Depois: `export PBI_TENANT_ID=... PBI_CLIENT_ID=... PBI_CLIENT_SECRET=...`.
- **Offline (sem Entra ID):** `python3 main.py --module powerbi --powerbi-offline ./artefatos_pbi` com arquivos PBIP/TMDL, PBIT, `model.bim`
  ou JSON do Scanner. É o modo que traz modo de armazenamento, relacionamentos e RLS exatos.

Detalhes em [`PERMISSIONS.md`](PERMISSIONS.md).

## Informações que a ferramenta não coleta

SLAs de latência, RTO/RPO, criticidade e donos dos dados, requisitos de residência e segurança, janela de corte e prazo
dependem de pessoas. Para o sizing e o plano de migração, responda o [`CUSTOMER_QUESTIONNAIRE.md`](CUSTOMER_QUESTIONNAIRE.md).
O que o relatório entrega para sizing e o que falta está em [`SIZING_AND_MIGRATION_INPUTS.md`](SIZING_AND_MIGRATION_INPUTS.md).

## Encerramento

Ao terminar, remova o acesso:

```bash
./scripts/teardown_assessment_sa.sh --project PROJETO_ID            # (use --org ORG_ID se criou em nível de organização)
```

Problemas comuns: [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).
