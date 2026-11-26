#!/usr/bin/env bash
# Remove os bindings, o papel customizado e a service account criados por setup_assessment_sa.sh.
# Use --dry-run para só imprimir. Revise antes de executar: a exclusão da service account é irreversível após 30 dias.
set -euo pipefail

PROJECT="" ORG="" EXTRA="" SA_NAME="assessment-reader" ROLE_ID="gcpDataAssessmentReader" DRY=0 YES=0 BILLING_DS=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;; --org) ORG="$2"; shift 2 ;; --extra-projects) EXTRA="$2"; shift 2 ;;
    --billing-dataset) BILLING_DS="$2"; shift 2 ;; --sa-name) SA_NAME="$2"; shift 2 ;; --role-id) ROLE_ID="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;; --yes) YES=1; shift ;;
    -h|--help) echo "Uso: $0 --project PROJECT_ID [--org ORG_ID] [--extra-projects P1,P2] [--billing-dataset P:D] [--sa-name N] [--role-id ID] [--dry-run] [--yes]"; exit 0 ;;
    *) echo "Opção desconhecida: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$PROJECT" ]] || { echo "--project é obrigatório" >&2; exit 2; }
SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"; member="serviceAccount:${SA_EMAIL}"

run() {
  local desc="$1"; shift
  echo; echo "# $desc"; printf '$'; printf ' %q' "$@"; echo
  [[ $DRY -eq 1 ]] && return 0
  if [[ $YES -eq 1 ]]; then "$@" || echo "(já removido ou inexistente)"; return 0; fi
  read -r -p "Executar? [y/N] " ans; [[ "$ans" == [yY]* ]] && { "$@" || echo "(já removido ou inexistente)"; } || echo "(ignorado)"
}

if [[ -n "$ORG" ]]; then
  for role in "organizations/${ORG}/roles/${ROLE_ID}" roles/accesscontextmanager.policyReader roles/orgpolicy.policyViewer roles/browser; do
    run "Remove $role da organização" gcloud organizations remove-iam-policy-binding "$ORG" --member "$member" --role "$role" --condition=None --format=none
  done
  run "Remove o papel customizado" gcloud iam roles delete "$ROLE_ID" --organization "$ORG" --quiet
else
  IFS=',' read -r -a projs <<< "${PROJECT},${EXTRA}"
  for p in "${projs[@]}"; do
    [[ -n "$p" ]] && run "Remove o papel de leitura de $p" gcloud projects remove-iam-policy-binding "$p" --member "$member" --role "projects/${p}/roles/${ROLE_ID}" --condition=None --format=none
  done
  run "Remove o papel customizado" gcloud iam roles delete "$ROLE_ID" --project "$PROJECT" --quiet
fi
run "Remove bigquery.jobUser" gcloud projects remove-iam-policy-binding "$PROJECT" --member "$member" --role roles/bigquery.jobUser --condition=None --format=none
[[ -n "$BILLING_DS" ]] && run "Remove o acesso ao Billing Export" bq remove-iam-policy-binding --member "$member" --role roles/bigquery.dataViewer "$BILLING_DS"
run "Apaga a service account" gcloud iam service-accounts delete "$SA_EMAIL" --project "$PROJECT" --quiet
