import pytest

from tests.fakes import RoutedSession
from utils.bq import BigQueryRunner, ColumnSelector, CostLimitExceeded, UnsafeQuery, assert_read_only, decode_row, info_schema
from utils.http import ApiClient, HttpSettings


def test_info_schema_qualifier():
    assert info_schema("p", "US", "JOBS_BY_PROJECT") == "`p`.`region-us`.INFORMATION_SCHEMA.JOBS_BY_PROJECT"
    assert info_schema("p", "southamerica-east1", "TABLES").endswith("`region-southamerica-east1`.INFORMATION_SCHEMA.TABLES")


def test_read_only_guard():
    assert_read_only("SELECT 1")
    assert_read_only("-- c\nWITH a AS (SELECT 1) SELECT * FROM a")
    assert_read_only("SELECT 'DELETE FROM x' AS s")  # literal não é instrução
    for sql in ("DELETE FROM t WHERE 1=1", "SELECT 1; DROP TABLE t", "CREATE TABLE t AS SELECT 1", "MERGE t USING s ON 1=1"):
        with pytest.raises(UnsafeQuery):
            assert_read_only(sql)


def test_decode_nested_rows():
    fields = [{"name": "a", "type": "INTEGER"}, {"name": "b", "type": "RECORD", "fields": [{"name": "x", "type": "STRING"}]},
              {"name": "c", "type": "STRING", "mode": "REPEATED"}, {"name": "t", "type": "TIMESTAMP"}, {"name": "f", "type": "BOOLEAN"}]
    row = {"f": [{"v": "5"}, {"v": {"f": [{"v": "y"}]}}, {"v": [{"v": "p"}, {"v": "q"}]}, {"v": "1.7E9"}, {"v": "true"}]}
    out = decode_row(row, fields)
    assert out["a"] == 5 and out["b"] == {"x": "y"} and out["c"] == ["p", "q"] and out["t"].startswith("2023-11") and out["f"] is True


def test_column_selector_nested_and_missing():
    s = ColumnSelector({"query_info": {"name": "query_info", "fields": [{"name": "query_hashes", "fields": [{"name": "normalized_literals"}]}]}})
    assert s.has("query_info.query_hashes.normalized_literals")
    assert s.col("edition") == "CAST(NULL AS STRING) AS edition"
    assert "edition" in s.missing


def _runner(estimate, max_bytes=100, total=1000):
    def dry(p, j):
        return {"statistics": {"query": {"totalBytesProcessed": str(estimate), "schema": {"fields": [{"name": "x", "type": "INTEGER"}]}}}}

    def query(p, j):
        assert j["maximumBytesBilled"] == str(max_bytes) and j["useLegacySql"] is False
        return {"jobComplete": True, "schema": {"fields": [{"name": "x", "type": "INTEGER"}]}, "rows": [{"f": [{"v": "1"}]}],
                "totalBytesBilled": "10485760", "jobReference": {"projectId": "p", "jobId": "j", "location": "US"}}
    api = ApiClient(RoutedSession([("POST", r"/jobs$", dry), ("POST", r"/queries$", query)]), HttpSettings(max_retries=0))
    return BigQueryRunner(api, max_bytes_billed=max_bytes, max_total_bytes_billed=total)


def test_query_respects_cost_cap():
    r = _runner(estimate=50, max_bytes=20 * 1024 * 1024, total=10 ** 12)
    assert r.query("p", "US", "SELECT 1") == [{"x": 1}]
    assert r.stats.queries == 1 and r.stats.dry_runs == 1
    r2 = _runner(estimate=10 ** 10, max_bytes=10 ** 9, total=10 ** 12)
    with pytest.raises(CostLimitExceeded):
        r2.query("p", "US", "SELECT 1")
    assert r2.stats.skipped_cost == 1 and r2.stats.queries == 0


def test_view_schema_via_dry_run():
    r = _runner(estimate=0)
    assert set(r.view_schema("p", "US", "`p`.`region-us`.INFORMATION_SCHEMA.TABLES")) == {"x"}
