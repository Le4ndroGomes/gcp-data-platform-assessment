#!/usr/bin/env bash
# Cria a service account de leitura do GCP Data Platform Assessment e concede o menor privilégio necessário.
#
# - Mostra cada comando antes de executar e pede confirmação (use --yes para não perguntar, --dry-run só para imprimir).
# - Não cria chave de service account (a ferramenta usa impersonação) e não habilita APIs.
# - É idempotente: reexecutar não duplica a service account, o papel nem os bindings.
# - Revise os comandos com o time de segurança antes de executar em produção.
set -euo pipefail

usage() {
  cat <<USAGE
Uso: $0 --project PROJECT_ID --executor MEMBER [opções]

Obrigatórios:
  --project ID           Projeto que hospeda a service account e onde as consultas de metadados (INFORMATION_SCHEMA) rodam
  --executor MEMBER      Quem vai executar a ferramenta e poderá assumir a SA (ex.: user:pessoa@empresa.com ou group:time@empresa.com)

Opcionais:
  --org ORG_ID           Cria o papel e os bindings na ORGANIZAÇÃO (cobre todos os projetos, folders, VPC-SC e Org Policies).
                         Sem --org, o papel e os bindings valem apenas para --project.
  --extra-projects LIST  Projetos adicionais (separados por vírgula) que receberão o mesmo papel (apenas sem --org)
  --billing-dataset P:D  Dataset do Billing Export (PROJETO:DATASET) para conceder bigquery.dataViewer somente nele
  --sa-name NAME         Nome da service account (padrão: assessment-reader)
  --role-id ID           ID do papel customizado (padrão: gcpDataAssessmentReader)
  --dry-run              Apenas imprime os comandos
  --yes                  Não pede confirmação a cada comando
  -h, --help             Esta ajuda
USAGE
}

PROJECT="" ORG="" EXECUTOR="" EXTRA="" BILLING_DS="" SA_NAME="assessment-reader" ROLE_ID="gcpDataAssessmentReader" DRY=0 YES=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    --org) ORG="$2"; shift 2 ;;
    --executor) EXECUTOR="$2"; shift 2 ;;
    --extra-projects) EXTRA="$2"; shift 2 ;;
    --billing-dataset) BILLING_DS="$2"; shift 2 ;;
    --sa-name) SA_NAME="$2"; shift 2 ;;
    --role-id) ROLE_ID="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --yes) YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Opção desconhecida: $1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ -n "$PROJECT" && -n "$EXECUTOR" ]] || { usage >&2; exit 2; }
[[ "$EXECUTOR" == *:* ]] || { echo "--executor deve ter o formato user:EMAIL, group:EMAIL ou serviceAccount:EMAIL" >&2; exit 2; }
command -v gcloud >/dev/null || { echo "gcloud não encontrado (use o Cloud Shell ou instale o Google Cloud CLI)." >&2; exit 1; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
if [[ -n "$ORG" ]]; then
  SCOPE="organization"; ROLE_FILE="$HERE/../config/iam/custom_role_organization.yaml"; ROLE_REF="organizations/${ORG}/roles/${ROLE_ID}"
else
  SCOPE="project"; ROLE_FILE="$HERE/../config/iam/custom_role_project.yaml"; ROLE_REF="projects/${PROJECT}/roles/${ROLE_ID}"
fi
[[ -f "$ROLE_FILE" ]] || { echo "Arquivo do papel não encontrado: $ROLE_FILE" >&2; exit 1; }

confirm() {
  [[ $YES -eq 1 || $DRY -eq 1 ]] && return 0
  read -r -p "Executar? [y/N] " ans; [[ "$ans" == [yY]* ]]
}
run() {  # run <descrição> <comando...>
  local desc="$1"; shift
  echo; echo "# $desc"; printf '$'; printf ' %q' "$@"; echo
  [[ $DRY -eq 1 ]] && return 0
  if confirm; then "$@"; else echo "(ignorado)"; fi
}
exists() { "$@" >/dev/null 2>&1; }

echo "Service account : $SA_EMAIL"
echo "Papel customizado: $ROLE_REF (escopo de $SCOPE)"
echo "Executor        : $EXECUTOR"
[[ $DRY -eq 1 ]] && echo "(dry-run: nada será alterado)"

# 1. Service account
if exists gcloud iam service-accounts describe "$SA_EMAIL" --project "$PROJECT"; then
  echo; echo "# Service account já existe; mantendo."
else
  run "Cria a service account (sem chave)" gcloud iam service-accounts create "$SA_NAME" --project "$PROJECT" \
    --display-name "GCP Data Platform Assessment (read-only)" \
    --description "Leitura de metadados para assessment; sem acesso a dados de negócio"
fi

# 2. Papel customizado. Em escopo de projeto o papel existe por projeto: é criado (ou atualizado) em cada um deles.
ensure_role() {  # ensure_role <flag de escopo> <valor>
  if exists gcloud iam roles describe "$ROLE_ID" "$1" "$2"; then
    run "Atualiza o papel customizado existente" gcloud iam roles update "$ROLE_ID" "$1" "$2" --file "$ROLE_FILE" --quiet
  else
    run "Cria o papel customizado (somente permissões de leitura de metadados)" gcloud iam roles create "$ROLE_ID" "$1" "$2" --file "$ROLE_FILE" --quiet
  fi
}

# 3. Bindings. --condition=None evita o prompt interativo quando a política já tem bindings condicionais.
#    --format=none evita imprimir a política IAM inteira.
member="serviceAccount:${SA_EMAIL}"
bind_project() {
  local p="$1"
  ensure_role --project "$p"
  run "Papel de leitura no projeto $p" gcloud projects add-iam-policy-binding "$p" \
    --member "$member" --role "projects/${p}/roles/${ROLE_ID}" --condition=None --format=none
}
if [[ "$SCOPE" == "organization" ]]; then
  ensure_role --organization "$ORG"
  run "Papel de leitura na organização" gcloud organizations add-iam-policy-binding "$ORG" --member "$member" --role "$ROLE_REF" --condition=None --format=none
  run "Leitura de VPC Service Controls" gcloud organizations add-iam-policy-binding "$ORG" --member "$member" --role roles/accesscontextmanager.policyReader --condition=None --format=none
  run "Leitura de Organization Policies" gcloud organizations add-iam-policy-binding "$ORG" --member "$member" --role roles/orgpolicy.policyViewer --condition=None --format=none
  run "Listar folders e projetos" gcloud organizations add-iam-policy-binding "$ORG" --member "$member" --role roles/browser --condition=None --format=none
else
  bind_project "$PROJECT"
  # shellcheck disable=SC2086
  for p in ${EXTRA//,/ }; do bind_project "$p"; done
fi
run "Executar jobs de metadados no projeto de execução" gcloud projects add-iam-policy-binding "$PROJECT" \
  --member "$member" --role roles/bigquery.jobUser --condition=None --format=none
if [[ -n "$BILLING_DS" ]]; then
  run "Ler somente o dataset do Billing Export" bq add-iam-policy-binding --member "$member" --role roles/bigquery.dataViewer "$BILLING_DS"
fi

# 4. Quem executa pode assumir a SA (impersonação). Nenhuma chave é criada.
run "Permite ao executor assumir a service account" gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --project "$PROJECT" --member "$EXECUTOR" --role roles/iam.serviceAccountTokenCreator --format=none

cat <<NEXT

Concluído. O binding de impersonação pode levar de 1 a 2 minutos para valer.
Próximo passo (na máquina de quem executa):

  gcloud auth application-default login
  python3 main.py --preflight --projects ${PROJECT} --impersonate-service-account ${SA_EMAIL}
NEXT
