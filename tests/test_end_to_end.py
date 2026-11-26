import json

import main


def test_demo_end_to_end(tmp_path):
    out = tmp_path / "out"
    assert main.main(["--demo", "--days", "30", "--output", str(out)]) == 0
    expected = ["assessment_report.md", "assessment_report.xlsx", "infrastructure_inventory.csv", "bigquery_datasets.csv",
                "bigquery_tables.csv", "bigquery_routines.csv", "workload_analysis.csv", "query_complexity.csv", "capacity_analysis.csv",
                "pipeline_inventory.csv", "cloud_storage_inventory.csv", "powerbi_inventory.csv", "powerbi_dependencies.csv",
                "powerbi_refresh_history.csv", "cost_analysis.csv", "security_findings.csv", "data_lineage.json", "data_lineage.graphml",
                "architecture_as_is.mmd", "migration_readiness.json", "databricks_sizing_inputs.json", "assessment_coverage.json",
                "missing_permissions.json", "collection_manifest.json", "execution.log"]
    for f in expected:
        assert (out / f).exists(), f
    md = (out / "assessment_report.md").read_text(encoding="utf-8")
    for section in ("Executive Summary", "Architecture AS-IS", "Platform Footprint", "Data Volume", "Workload Distribution",
                    "Pipeline Complexity", "Power BI Footprint", "Cost Breakdown", "Security & Governance", "Migration Readiness",
                    "Sizing Inputs", "Risks & Recommendations", "Assessment Coverage"):
        assert f"## " in md and section in md
    manifest = json.loads((out / "collection_manifest.json").read_text())
    assert all("sha256" in f for f in manifest["files"])
    # anonimização: nenhum e-mail fictício de pessoa deve aparecer nos entregáveis
    for f in ("assessment_report.md", "workload_analysis.csv", "security_findings.csv"):
        assert "analista01@redeexemplo.com.br" not in (out / f).read_text(encoding="utf-8-sig")
    # analyze-only reproduz o relatório a partir do raw
    assert main.main(["--analyze-only", "--input", str(out)]) == 0


def test_focus_use_case(tmp_path):
    out = tmp_path / "o"
    scope = tmp_path / "scope.yaml"
    scope.write_text("name: Faturamento\nseeds:\n  powerbi_datasets: ['Faturamento Hospitalar']\ndirection: upstream\n", encoding="utf-8")
    assert main.main(["--demo", "--days", "30", "--output", str(out), "--focus-file", str(scope)]) == 0
    uc = json.loads((out / "use_case_assessment.json").read_text())
    assert uc["semantic_models"] == 1 and uc["tables"] > 0
    assert "storage_denominator" in uc["share_of_platform"]
