"""Fakes reutilizáveis: sessão HTTP roteada por URL e respostas Google/Power BI."""
from __future__ import annotations

import json
import re


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.content = b"" if body is None and status == 204 else json.dumps(self._body).encode()
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class RoutedSession:
    """routes: lista de (método, regex, callable(params, json) -> FakeResponse | dict)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.calls.append((method, url, params, json))
        for m, rx, handler in self.routes:
            if m == method and re.search(rx, url):
                out = handler(params or {}, json)
                return out if isinstance(out, FakeResponse) else FakeResponse(200, out)
        return FakeResponse(404, {"error": {"code": 404, "message": f"no route {method} {url}", "status": "NOT_FOUND"}})


def denied(service_disabled=False):
    if service_disabled:
        return FakeResponse(403, {"error": {"code": 403, "message": "API has not been used in project 1 before or it is disabled",
                                            "status": "PERMISSION_DENIED",
                                            "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "SERVICE_DISABLED"}]}})
    return FakeResponse(403, {"error": {"code": 403, "message": "The caller does not have permission", "status": "PERMISSION_DENIED"}})
