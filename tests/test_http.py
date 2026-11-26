import pytest

from tests.fakes import FakeResponse, RoutedSession, denied
from utils.http import ApiClient, ApiDisabled, HttpSettings, NotFound, PermissionDenied, WriteBlocked


def client(routes, **kw):
    return ApiClient(RoutedSession(routes), HttpSettings(max_retries=3, backoff_base=0.01), sleep=lambda s: None, **kw)


def test_pagination_google_style():
    pages = {None: {"items": [1, 2], "nextPageToken": "t1"}, "t1": {"items": [3], "nextPageToken": "t2"}, "t2": {"items": [4]}}
    c = client([("GET", r"/list$", lambda p, j: pages[p.get("pageToken")])])
    assert list(c.paginate("https://x.googleapis.com/list", "items")) == [1, 2, 3, 4]


def test_pagination_max_items():
    c = client([("GET", r"/list$", lambda p, j: {"items": list(range(10)), "nextPageToken": "x"})])
    assert len(list(c.paginate("https://x.googleapis.com/list", "items", max_items=5))) == 5


def test_retry_on_429_then_success():
    state = {"n": 0}

    def h(p, j):
        state["n"] += 1
        return FakeResponse(429, {"error": {"code": 429, "message": "rate", "status": "RESOURCE_EXHAUSTED"}},
                            headers={"Retry-After": "0"}) if state["n"] < 3 else {"ok": True}
    c = client([("GET", r"/r$", h)])
    assert c.get("https://x.googleapis.com/r") == {"ok": True}
    assert state["n"] == 3


def test_retry_exhausted_raises():
    c = client([("GET", r"/r$", lambda p, j: FakeResponse(503, {"error": {"code": 503, "message": "down"}}))])
    with pytest.raises(Exception):
        c.get("https://x.googleapis.com/r")


def test_error_classification():
    c = client([("GET", r"/denied$", lambda p, j: denied()), ("GET", r"/disabled$", lambda p, j: denied(True))])
    with pytest.raises(PermissionDenied):
        c.get("https://x.googleapis.com/denied")
    with pytest.raises(ApiDisabled):
        c.get("https://x.googleapis.com/disabled")
    with pytest.raises(NotFound):
        c.get("https://x.googleapis.com/nothing")


def test_write_methods_blocked():
    c = client([])
    for m in ("PUT", "PATCH", "DELETE"):
        with pytest.raises(WriteBlocked):
            c.request(m, "https://x.googleapis.com/a")
    with pytest.raises(WriteBlocked):
        c.post("https://bigquery.googleapis.com/bigquery/v2/projects/p/datasets", json_body={})


def test_post_allowlist_permits_read_endpoints():
    c = client([("POST", r"/queries$", lambda p, j: {"jobComplete": True}), ("POST", r":testIamPermissions$", lambda p, j: {"permissions": []})])
    assert c.post("https://bigquery.googleapis.com/bigquery/v2/projects/p/queries", json_body={})["jobComplete"]
    assert c.post("https://cloudresourcemanager.googleapis.com/v3/projects/p:testIamPermissions", json_body={}) == {"permissions": []}
