"""Collectors contra APIs simuladas: paginação, permissão negada parcial e continuidade."""
from core.context import DEFAULT_CONFIG, Context
from core.coverage import CoverageTracker
from tests.fakes import RoutedSession, denied
from utils.bq import BigQueryRunner
from utils.http import ApiClient, HttpSettings
from utils.redaction import Redactor


def make_ctx(tmp_path, routes):
    import copy

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    ctx = Context(config=cfg, output_dir=tmp_path, coverage=CoverageTracker(), redactor=Redactor(), days=30)
    ctx.gcp = ApiClient(RoutedSession(routes), HttpSettings(max_retries=0), sleep=lambda s: None)
    ctx.bq = BigQueryRunner(ctx.gcp, max_bytes_billed=10 ** 9, max_total_bytes_billed=10 ** 10)
    return ctx


def test_resolve_projects_and_partial_permissions(tmp_path):
    from collectors import discovery

    routes = [
        ("GET", r"/v3/projects:search", lambda p, j: {"projects": [{"projectId": "prj-prd", "name": "projects/1", "state": "ACTIVE"},
                                                                  {"projectId": "sys-abc", "name": "projects/2", "state": "ACTIVE"},
                                                                  {"projectId": "prj-dev", "name": "projects/3", "state": "ACTIVE"}]}),
        ("GET", r"/projects/prj-prd/datasets", lambda p, j: {"datasets": [{"datasetReference": {"datasetId": "d1"}, "location": "US"}]}),
        ("GET", r"/projects/prj-dev/datasets", lambda p, j: denied()),
    ]
    ctx = make_ctx(tmp_path, routes)
    projects = discovery.resolve_projects(ctx)
    sel = {p["project_id"]: p for p in projects}
    assert sel["sys-abc"]["selected"] is False  # padrão de exclusão
    assert sel["prj-prd"]["bigquery_locations"] == ["US"] and sel["prj-prd"]["environment"] == "prod"
    assert sel["prj-dev"]["bigquery_locations"] == []
    st = {e["component"]: e["status"] for e in ctx.coverage.entries()}
    assert st["bigquery_locations"] == "Partial"


def test_bigquery_metadata_handles_missing_views(tmp_path):
    from collectors import bigquery_metadata

    def dry(p, j):
        sql = j["configuration"]["query"]["query"]
        if "TABLE_STORAGE" in sql or "ROW_ACCESS" in sql:
            return denied()
        return {"statistics": {"query": {"totalBytesProcessed": "0", "schema": {"fields": [
            {"name": "table_schema"}, {"name": "table_name"}, {"name": "table_type"}, {"name": "schema_name"}, {"name": "location"}]}}}}

    def query(p, j):
        if "SCHEMATA" in j["query"] and "OPTIONS" not in j["query"]:
            return {"jobComplete": True, "schema": {"fields": [{"name": "schema_name", "type": "STRING"}]},
                    "rows": [{"f": [{"v": "d1"}]}], "jobReference": {"projectId": "p", "jobId": "j"}}
        return {"jobComplete": True, "schema": {"fields": []}, "rows": [], "jobReference": {"projectId": "p", "jobId": "j"}}

    ctx = make_ctx(tmp_path, [("POST", r"/jobs$", dry), ("POST", r"/queries$", query)])
    ctx.projects = [{"project_id": "p", "selected": True, "bigquery_locations": ["US"]}]
    bigquery_metadata.collect(ctx)
    meta = ctx.load_raw("bigquery", "metadata__p__US")
    assert meta["schemata"] == [{"schema_name": "d1"}]
    assert "table_storage" not in meta  # visão indisponível não interrompe
    cov = {e["component"]: e for e in ctx.coverage.entries()}
    assert any("TABLE_STORAGE indisponível" in l for l in cov["volumetry"]["limitations"])
    assert ctx.bq.stats.queries >= 1


def test_pipelines_api_disabled_is_absence(tmp_path):
    from collectors import pipelines

    ctx = make_ctx(tmp_path, [])
    ctx.projects = [{"project_id": "p", "selected": True, "bigquery_locations": []}]
    ctx.save_raw("discovery", "services_enabled", {"p": ["bigquery.googleapis.com"]})
    ctx.save_raw("discovery", "assets", {"p": [{"service": "bigquery", "location": "us"}]})
    pipelines.collect(ctx)
    st = {e["component"]: e for e in ctx.coverage.entries()}
    assert st["composer"]["status"] == "Complete" and st["composer"]["resources_found"] == 0
    assert st["dataflow"]["visibility_note"].startswith("Ausência de recursos")
