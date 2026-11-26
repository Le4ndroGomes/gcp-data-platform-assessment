"""Cliente HTTP somente-leitura com retries, backoff exponencial, timeouts,
tratamento de rate limit e paginação.

Política de segurança: apenas GET é permitido por padrão. POST só é aceito
para endpoints explicitamente listados em `READ_ONLY_POST_ALLOWLIST` (consultas,
dry runs, testIamPermissions, buscas e o scanner do Power BI), todos sem efeito
sobre a configuração do ambiente.
"""
from __future__ import annotations

import json
import random
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from utils.logging import get_logger

log = get_logger("http")

# Padrões de URL em que POST é uma operação de leitura.
READ_ONLY_POST_ALLOWLIST = [
    re.compile(r"bigquery\.googleapis\.com/bigquery/v2/projects/[^/]+/queries$"),  # jobs.query (SELECT)
    re.compile(r"bigquery\.googleapis\.com/bigquery/v2/projects/[^/]+/jobs$"),  # jobs.insert (dry run)
    re.compile(r":testIamPermissions$"),
    re.compile(r":getIamPolicy$"),
    re.compile(r":searchLinks$"),
    re.compile(r":searchLineageProcesses$"),
    re.compile(r"/compilationResults/[^/]+:query$"),
    re.compile(r"api\.powerbi\.com/v1\.0/myorg/admin/workspaces/getInfo"),
]


class ApiError(Exception):
    def __init__(self, status: int, message: str, url: str, reason: str = "", body: Any = None):
        super().__init__(f"HTTP {status} {reason}: {message} [{_short(url)}]")
        self.status = status
        self.message = message
        self.url = url
        self.reason = reason
        self.body = body

    @property
    def kind(self) -> str:
        if self.status in (401,):
            return "unauthenticated"
        if self.reason in ("SERVICE_DISABLED", "accessNotConfigured", "API_DISABLED"):
            return "api_disabled"
        if self.status == 403:
            return "permission_denied"
        if self.status == 404:
            return "not_found"
        if self.status == 400:
            return "invalid_argument"
        if self.status == 429:
            return "rate_limited"
        return "error"


class PermissionDenied(ApiError):
    pass


class ApiDisabled(ApiError):
    pass


class NotFound(ApiError):
    pass


class WriteBlocked(Exception):
    """Tentativa de operação de escrita bloqueada pela política somente-leitura."""


def _short(url: str) -> str:
    return url.split("?")[0][:160]


def _parse_error(resp) -> tuple[str, str, Any]:
    try:
        body = resp.json()
    except Exception:
        return (resp.text or "")[:500], "", None
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        message = err.get("message") or json.dumps(err)[:500]
        reason = err.get("status") or ""
        for d in err.get("details", []) or []:
            if isinstance(d, dict) and d.get("reason"):
                reason = d["reason"]
        for e in err.get("errors", []) or []:
            if isinstance(e, dict) and e.get("reason"):
                reason = e["reason"] if reason in ("", "PERMISSION_DENIED") else reason
        # Power BI: {"error": {"code": "...", "message": "..."}}
        if not reason and isinstance(err.get("code"), str):
            reason = err["code"]
        return message, reason, body
    if isinstance(err, str):
        return body.get("error_description", err), err, body
    return json.dumps(body)[:500], "", body


RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
RETRYABLE_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "backendError", "RESOURCE_EXHAUSTED"}


@dataclass
class HttpSettings:
    timeout: float = 60.0
    max_retries: int = 5
    backoff_base: float = 1.0
    backoff_max: float = 60.0
    max_in_flight: int = 8
    allow_post_allowlist: bool = True


class ApiClient:
    """Wrapper sobre um `requests.Session` (ou `google.auth.transport.requests.AuthorizedSession`)."""

    def __init__(self, session, settings: HttpSettings | None = None, name: str = "api",
                 sleep: Callable[[float], None] = time.sleep):
        self.session = session
        self.settings = settings or HttpSettings()
        self.name = name
        self._sem = threading.BoundedSemaphore(self.settings.max_in_flight)
        self._sleep = sleep
        self.request_count = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ core
    def request(self, method: str, url: str, *, params: dict | None = None, json_body: Any = None,
                headers: dict | None = None, timeout: float | None = None) -> Any:
        method = method.upper()
        if method not in ("GET", "POST"):
            raise WriteBlocked(f"Método {method} bloqueado (modo somente-leitura): {_short(url)}")
        if method == "POST" and not (self.settings.allow_post_allowlist and
                                     any(p.search(url.split("?")[0]) for p in READ_ONLY_POST_ALLOWLIST)):
            raise WriteBlocked(f"POST bloqueado (endpoint fora da allowlist de leitura): {_short(url)}")

        attempt = 0
        while True:
            attempt += 1
            with self._sem:
                with self._lock:
                    self.request_count += 1
                try:
                    resp = self.session.request(method, url, params=params, json=json_body, headers=headers,
                                                timeout=timeout or self.settings.timeout)
                except Exception as exc:  # erros de rede / timeout
                    if attempt <= self.settings.max_retries:
                        delay = self._backoff(attempt)
                        log.debug("Erro de rede em %s (%s); retry %s em %.1fs", _short(url), exc, attempt, delay)
                        self._sleep(delay)
                        continue
                    raise ApiError(0, str(exc), url, "network") from exc

            if 200 <= resp.status_code < 300:
                if resp.status_code == 204 or not resp.content:
                    return {}
                try:
                    return resp.json()
                except ValueError:
                    return {"_raw": resp.text}

            message, reason, body = _parse_error(resp)
            retryable = resp.status_code in RETRYABLE_STATUS or reason in RETRYABLE_REASONS
            if retryable and attempt <= self.settings.max_retries:
                delay = self._retry_after(resp) or self._backoff(attempt)
                log.debug("HTTP %s (%s) em %s; retry %s em %.1fs", resp.status_code, reason, _short(url), attempt, delay)
                self._sleep(delay)
                continue
            raise self._make_error(resp.status_code, message, url, reason, body)

    @staticmethod
    def _make_error(status, message, url, reason, body) -> ApiError:
        if reason in ("SERVICE_DISABLED", "accessNotConfigured", "API_DISABLED") or \
                (status == 403 and "has not been used in project" in (message or "")):
            return ApiDisabled(status, message, url, reason or "SERVICE_DISABLED", body)
        if status == 403 or status == 401:
            return PermissionDenied(status, message, url, reason, body)
        if status == 404:
            return NotFound(status, message, url, reason, body)
        return ApiError(status, message, url, reason, body)

    def _backoff(self, attempt: int) -> float:
        base = min(self.settings.backoff_max, self.settings.backoff_base * (2 ** (attempt - 1)))
        return base * (0.5 + random.random() / 2)

    def _retry_after(self, resp) -> float | None:
        value = resp.headers.get("Retry-After") if hasattr(resp, "headers") else None
        if not value:
            return None
        try:
            return min(float(value), self.settings.backoff_max * 5)
        except ValueError:
            return None

    def get(self, url: str, params: dict | None = None, **kw) -> Any:
        return self.request("GET", url, params=params, **kw)

    def post(self, url: str, json_body: Any = None, params: dict | None = None, **kw) -> Any:
        return self.request("POST", url, params=params, json_body=json_body, **kw)

    # ------------------------------------------------------------ pagination
    def paginate(self, url: str, items_key: str, params: dict | None = None, *,
                 token_param: str = "pageToken", next_field: str = "nextPageToken",
                 max_items: int | None = None, method: str = "GET", body: dict | None = None) -> Iterator[dict]:
        """Paginação no padrão Google (pageToken/nextPageToken)."""
        params = dict(params or {})
        count = 0
        while True:
            if method == "GET":
                page = self.get(url, params=params)
            else:
                page = self.post(url, json_body=body, params=params)
            items = page.get(items_key, []) or []
            for it in items:
                yield it
                count += 1
                if max_items and count >= max_items:
                    return
            token = page.get(next_field)
            if not token:
                return
            if method == "GET":
                params[token_param] = token
            else:
                body = dict(body or {})
                body[token_param] = token

    def paginate_aggregated(self, url: str, items_key: str, params: dict | None = None,
                            max_items: int | None = None) -> Iterator[tuple[str, dict]]:
        """Paginação de listas agregadas da Compute API (items: {scope: {items_key: [...]}})."""
        params = dict(params or {})
        count = 0
        while True:
            page = self.get(url, params=params)
            for scope, block in (page.get("items") or {}).items():
                for it in (block or {}).get(items_key, []) or []:
                    yield scope, it
                    count += 1
                    if max_items and count >= max_items:
                        return
            token = page.get("nextPageToken")
            if not token:
                return
            params["pageToken"] = token

    def paginate_odata(self, url: str, params: dict | None = None, max_items: int | None = None) -> Iterator[dict]:
        """Paginação OData (Power BI): `value` + `@odata.nextLink`/`continuationUri`."""
        next_url, next_params = url, params
        count = 0
        while next_url:
            page = self.get(next_url, params=next_params)
            items = page.get("value") if "value" in page else page.get("activityEventEntities", [])
            for it in items or []:
                yield it
                count += 1
                if max_items and count >= max_items:
                    return
            next_url = page.get("@odata.nextLink") or (None if page.get("lastResultSet", True) else page.get("continuationUri"))
            next_params = None
