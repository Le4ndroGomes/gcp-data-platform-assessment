from pathlib import Path

from analyzers.migration_readiness import analyze as readiness
from core.coverage import CoverageTracker
from utils.http import ApiDisabled, NotFound, PermissionDenied

BASE = Path(__file__).resolve().parent.parent


def test_coverage_status_transitions():
    c = CoverageTracker()
    c.ok("m", "complete", 0)
    c.finalize("m", "complete")
    c.ok("m", "partial", 3)
    c.fail("m", "partial", PermissionDenied(403, "x", "https://bigquery.googleapis.com/a"), scope="p2")
    c.finalize("m", "partial")
    c.fail("m", "denied", PermissionDenied(403, "x", "https://bigquery.googleapis.com/a"))
    c.finalize("m", "denied")
    c.fail("m", "na", ApiDisabled(403, "x", "https://dataform.googleapis.com/a", "SERVICE_DISABLED"))
    c.finalize("m", "na")
    c.fail("m", "nf", NotFound(404, "x", "https://x.googleapis.com/a"))
    c.finalize("m", "nf")
    st = {e["component"]: e for e in c.entries()}
    assert st["complete"]["status"] == "Complete" and "Ausência de recursos confirmada" in st["complete"]["visibility_note"]
    assert st["partial"]["status"] == "Partial"
    assert st["denied"]["status"] == "Permission Denied" and "NÃO significa" in st["denied"]["visibility_note"]
    assert st["na"]["status"] == "Not Available" and st["na"]["unavailable_apis"] == ["dataform.googleapis.com"]
    assert st["nf"]["status"] == "Complete"
    assert any(m["type"] == "permission_denied" for m in c.missing())


def _empty():
    return dict(volume={"logical": {"TB": 0, "bytes": 0}, "long_term_logical": {"bytes": 0}, "tables_counted": 0},
                sql={"objects": []}, pipelines={"total": 0}, pbi={}, security={}, footprint={"services": []})


def test_readiness_excludes_missing_evidence():
    args = _empty()
    args["volume"] = {"logical": {"TB": 5, "bytes": 5e12}, "long_term_logical": {"bytes": 4e12}, "tables_counted": 10}
    r = readiness({"projects": []}, {"readiness_weights": {"data_volume": 15, "sql_complexity": 20, "pipeline_complexity": 20,
                                                           "bi_coupling": 15, "platform_specific_features": 15, "security_governance": 15},
                                     "databricks": {}}, coverage=[], base_dir=BASE, **args)
    assert r["dimensions"]["data_volume"]["score"] == 95
    assert r["dimensions"]["bi_coupling"]["score"] is None
    assert r["dimensions"]["sql_complexity"]["score"] is None
    # só data_volume (15) e platform_specific_features (15) têm evidência => cobertura 30%
    assert r["evidence_coverage_pct"] == 30.0
    assert r["score"] is not None


def test_readiness_no_evidence_no_score():
    r = readiness({"projects": []}, {"readiness_weights": {"data_volume": 1}, "databricks": {}}, coverage=[], base_dir=BASE, **_empty())
    assert r["score"] is None and r["evidence_coverage_pct"] == 0


def test_region_snapshot_flags_brazil():
    r = readiness({"projects": [{"bigquery_locations": ["southamerica-east1"]}]}, {"readiness_weights": {}, "databricks": {}},
                  coverage=[], base_dir=BASE, **_empty())
    reg = r["regions"]["regions"][0]
    assert reg["supported"] and "lakeflow_connect_managed" in reg["missing_features"]
