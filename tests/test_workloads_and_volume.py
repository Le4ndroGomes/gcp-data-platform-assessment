from collections import Counter

from analyzers import volume_analysis, workload_analysis
from analyzers.workload_analysis import classify_principal, percentile_from_hist
from core.context import DEFAULT_CONFIG


def test_percentile_from_histogram():
    h = Counter({3: 50, 4: 50})  # buckets [500,1000) e [1000,2000)
    assert 500 <= percentile_from_hist(h, 0.5) <= 1000
    assert 1000 <= percentile_from_hist(h, 0.95) <= 2000
    assert percentile_from_hist(Counter(), 0.5) is None


def test_classification_rules():
    r = DEFAULT_CONFIG["workload_rules"]
    assert classify_principal({"principal": "x", "ml_statements": 3, "jobs": 10}, {}, r)[0] == "Machine Learning"
    assert classify_principal({"principal": "x", "matches_bi": True, "jobs": 10}, {}, r)[0] == "Business Intelligence"
    assert classify_principal({"principal": "x", "jobs": 10}, {"x": ["airflow-dag=d1"]}, r)[0] == "Data Engineering e ETL"
    assert classify_principal({"principal": "x", "jobs": 10, "write_statements": 8, "is_service_account": True}, {}, r)[0] == "Data Warehousing"
    assert classify_principal({"principal": "x", "jobs": 10, "is_service_account": False}, {}, r)[0] == "Ad hoc Analytics"


def _wl():
    hourly = [{"hour": "2026-11-01T12:00:00+00:00", "job_type": "QUERY", "priority": "INTERACTIVE", "jobs": 10, "statements": 12,
               "scripts": 1, "child_jobs": 2, "failed": 1, "cache_hits": 3, "bytes_processed": 100, "bytes_billed": 100,
               "on_demand_bytes_billed": 60, "slot_ms": 3_600_000, "sum_run_ms": 1000, "max_run_ms": 500, "sum_queue_ms": 10}]
    p = {"principal": "u1", "jobs": 5, "slot_ms": 100, "bytes_billed": 1, "is_service_account": False}
    return [{"project_id": "a", "location": "US", "window_days": 1, "hourly": hourly, "principals": [p]},
            {"project_id": "b", "location": "EU", "window_days": 1, "hourly": hourly, "principals": [dict(p)]}]


def test_workload_totals_and_principal_dedup():
    out = workload_analysis.analyze({"workloads": _wl()}, DEFAULT_CONFIG)
    t = out["totals"]
    assert t["jobs"] == 20 and t["statements"] == 24 and t["on_demand_bytes_billed"] == 120
    assert out["slots"]["slot_hours"] == 2.0
    assert len(out["consumers"]) == 1 and out["consumers"][0]["jobs"] == 10
    assert out["classes"][0]["principals"] == 1


def test_volume_no_double_count_and_no_invented_growth():
    tables = {
        "p.d.base": {"ref": "p.d.base", "project_id": "p", "dataset": "d", "table_type": "BASE TABLE", "total_logical_bytes": 100,
                     "total_physical_bytes": 20, "total_rows": 10},
        "p.d.snap": {"ref": "p.d.snap", "project_id": "p", "dataset": "d", "table_type": "SNAPSHOT", "total_logical_bytes": 100,
                     "total_physical_bytes": 2},
        "p.d.view": {"ref": "p.d.view", "project_id": "p", "dataset": "d", "table_type": "VIEW"},
        "p.d.gone": {"ref": "p.d.gone", "project_id": "p", "dataset": "d", "table_type": "BASE TABLE", "deleted": True, "total_logical_bytes": 999},
    }
    v = volume_analysis.analyze({"tables_by_ref": tables}, DEFAULT_CONFIG)
    assert v["logical"]["bytes"] == 100 and v["snapshot_clone_physical"]["bytes"] == 2 and v["total_rows"] == 10
    assert v["growth"]["coverage_days"] == 0 and v["growth"][30] is None
