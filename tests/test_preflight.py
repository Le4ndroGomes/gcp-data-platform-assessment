import copy

from core.context import DEFAULT_CONFIG, Context
from core.coverage import CoverageTracker
from tests.fakes import RoutedSession, denied
from utils.bq import BigQueryRunner
from utils.http import ApiClient, HttpSettings
from utils.permissions import custom_role_yaml, matrix_markdown, preflight, preflight_markdown
from utils.redaction import Redactor


class _Ident:
    principal_hash, method, quota_project = "abc", "adc", "q"


def test_preflight_reports_missing_permissions_and_cost(tmp_path):
    def test_perms(p, j):
        return {"permissions": [x for x in j["permissions"] if not x.startswith("bigquery.jobs.listAll")]}

    def dry(p, j):
        sql = j["configuration"]["query"]["query"]
        if "TABLE_STORAGE" in sql:
            return denied()
        return {"statistics": {"query": {"totalBytesProcessed": str(30 * 10 ** 9), "schema": {"fields": [{"name": "x"}]}}}}

    routes = [("GET", r"/v3/projects/p1$", lambda p, j: {"projectId": "p1", "name": "projects/1", "state": "ACTIVE"}),
              ("GET", r"/projects/p1/datasets", lambda p, j: {"datasets": [{"datasetReference": {"datasetId": "d"}, "location": "US"}]}),
              ("GET", r"/projects/p1/services", lambda p, j: {"services": [{"config": {"name": "bigquery.googleapis.com"}}]}),
              ("POST", r":testIamPermissions$", test_perms), ("POST", r"/jobs$", dry)]
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["gcp"]["projects"] = ["p1"]
    cfg["bigquery"]["max_bytes_billed_per_query"] = 10 ** 10
    ctx = Context(config=cfg, output_dir=tmp_path, coverage=CoverageTracker(), redactor=Redactor(), days=90)
    ctx.gcp = ApiClient(RoutedSession(routes), HttpSettings(max_retries=0), sleep=lambda s: None)
    ctx.bq = BigQueryRunner(ctx.gcp, max_bytes_billed=10 ** 10, max_total_bytes_billed=10 ** 12)
    ctx.identity = _Ident()
    rep = preflight(ctx, ["bigquery"])
    p = rep["projects"][0]
    assert "bigquery.jobs.listAll" in p["permissions_missing"]
    assert p["bigquery_views"]["US:TABLE_STORAGE"] is False and p["bigquery_views"]["US:JOBS_BY_PROJECT"] is True
    est = p["cost_estimate"][0]
    assert est["bytes_total"] == 13 * 30 * 10 ** 9 and est["exceeds_per_query_cap"] is True
    md = preflight_markdown(rep)
    assert "Estimativa de custo" in md and "bigquery.jobs.listAll" in md and "setup_assessment_sa.sh" in md


def test_matrix_and_custom_role():
    md = matrix_markdown(principal="serviceAccount:x@y", org_id="123")
    assert "roles/bigquery.metadataViewer" in md and "--organization=123" in md and "Tenant setting" in md
    role = custom_role_yaml()
    assert "bigquery.jobs.listAll" in role and "storage.objects.get" not in role
