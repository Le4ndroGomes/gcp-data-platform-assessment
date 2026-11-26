"""Regressões encontradas na primeira execução real (projeto sandbox, impersonação de service account)."""
import json
import re

import main
from collectors.bigquery_metadata import sensitive_regex
from collectors.services import _regional
from exporters.markdown_report import NB, blind
from utils.authentication import GCP_SCOPES


def test_gcp_scope_is_cloud_platform():
    # `cloud-platform.read-only` causa 403 "insufficient authentication scopes" em IAM, Cloud Asset, Pub/Sub, Compute e
    # Monitoring quando o token é emitido por impersonação. A somente leitura é garantida por IAM mínimo + cliente HTTP.
    assert GCP_SCOPES == ["https://www.googleapis.com/auth/cloud-platform"]


def test_sensitive_regex_has_single_capture_group():
    # BigQuery: "Regular expressions passed into extraction functions must not have more than 1 capturing group"
    rx = sensitive_regex(["cpf", "e_mail", "cartao_sus", "nome"])
    assert re.compile(rx).groups == 1
    assert re.search(rx, "paciente_cpf_2").group(1) == "cpf"
    assert re.search(rx, "cartao_sus").group(1) == "cartao_sus"
    assert not re.search(rx, "descricao")  # sem falso positivo por substring


def test_regional_filter_drops_bigquery_multiregions():
    assert _regional(["US", "EU", "us-central1", "southamerica-east1", "asia-northeast1"]) == [
        "asia-northeast1", "southamerica-east1", "us-central1"]


def test_blind_when_component_missing_or_denied():
    cov = [{"module": "m", "component": "ok", "status": "Complete"},
           {"module": "m", "component": "denied", "status": "Permission Denied"},
           {"module": "m", "component": "partial", "status": "Partial"}]
    assert blind(cov, "m", "denied") and blind(cov, "m", "ausente")
    assert not blind(cov, "m", "ok") and not blind(cov, "m", "partial")


def test_report_does_not_show_zero_when_blind(tmp_path):
    out = tmp_path / "out"
    assert main.main(["--demo", "--days", "30", "--output", str(out)]) == 0
    entries = json.loads((out / "assessment_coverage.json").read_text(encoding="utf-8"))
    hidden = {("cloud_storage", "volume_metrics"), ("discovery", "service_accounts"), ("discovery", "network"),
              ("security", "policy_tags"), ("security", "service_account_keys")}
    for e in entries:
        if (e["module"], e["component"]) in hidden:
            e["status"] = "Permission Denied"
    (out / "raw" / "_coverage.json").write_text(json.dumps({"entries": entries, "missing": []}), encoding="utf-8")
    assert main.main(["--analyze-only", "--input", str(out)]) == 0
    md = (out / "assessment_report.md").read_text(encoding="utf-8")
    assert NB in md
    assert "Rede (VPC, VPN, Interconnect, NAT) sem visibilidade" in md
    assert "Clusters SQL" not in md  # heurística removida: não há conversão confiável de concorrência em clusters
    assert "### Volume por localização" in md


def test_custom_role_files_match_generator():
    """Os YAMLs versionados (usados pelo script de setup) não podem divergir da matriz de permissões."""
    from pathlib import Path

    from utils.permissions import MATRIX, ORG_ONLY_PERMISSIONS, custom_role_yaml

    base = Path(__file__).resolve().parent.parent / "config" / "iam"
    for scope, fname in (("project", "custom_role_project.yaml"), ("organization", "custom_role_organization.yaml")):
        assert (base / fname).read_text(encoding="utf-8").strip() == custom_role_yaml(scope).strip(), f"{fname} desatualizado: " \
            f"rode `python3 main.py --print-custom-role {scope} > config/iam/{fname}`"
    project = (base / "custom_role_project.yaml").read_text(encoding="utf-8")
    assert not any(p in project for p in ORG_ONLY_PERMISSIONS)
    # nenhuma permissão de leitura de dados nem de escrita no papel
    perms = {line[2:] for line in project.splitlines() if line.startswith("- ")}
    assert not [p for p in perms if p.endswith((".getData", ".create", ".update", ".delete", ".setIamPolicy"))
                and p != "bigquery.jobs.create"]
    assert "bigquery.bireservations.get" in perms
    assert all(p in perms for m in MATRIX.values() for p in m["permissions"])


def test_cloud_run_is_listed_per_region():
    # A API v2 do Cloud Run rejeita o curinga `-` em locations (INVALID_ARGUMENT)
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "collectors" / "pipelines.py").read_text(encoding="utf-8")
    assert "locations/-/services" not in src and "locations/-/jobs" not in src.split("def _connections")[0].split("RUN}")[-1]
