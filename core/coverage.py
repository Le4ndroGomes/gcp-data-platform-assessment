"""Registro de cobertura do assessment (item 19) e de permissões ausentes.

Diferencia explicitamente:
  - ausência de recursos   -> status Complete com resources_found == 0
  - ausência de visibilidade -> Permission Denied / Not Available / Partial
"""
from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

COMPLETE = "Complete"
PARTIAL = "Partial"
PERMISSION_DENIED = "Permission Denied"
NOT_AVAILABLE = "Not Available"
NOT_APPLICABLE = "Not Applicable"
NOT_ASSESSED = "Not Assessed"
STATUSES = [COMPLETE, PARTIAL, PERMISSION_DENIED, NOT_AVAILABLE, NOT_APPLICABLE, NOT_ASSESSED]

# Permissão/papel sugerido por API (usado nas mensagens de permissões ausentes).
API_ROLE_HINTS = {
    "cloudresourcemanager.googleapis.com": "roles/browser (ou roles/resourcemanager.folderViewer na organização)",
    "serviceusage.googleapis.com": "roles/serviceusage.serviceUsageViewer",
    "cloudasset.googleapis.com": "roles/cloudasset.viewer",
    "cloudbilling.googleapis.com": "roles/billing.viewer (na billing account)",
    "iam.googleapis.com": "roles/iam.securityReviewer",
    "compute.googleapis.com": "roles/compute.networkViewer",
    "bigquery.googleapis.com": "roles/bigquery.metadataViewer + roles/bigquery.resourceViewer + roles/bigquery.jobUser",
    "bigquerydatatransfer.googleapis.com": "bigquery.transfers.get (papel customizado de leitura)",
    "bigqueryreservation.googleapis.com": "roles/bigquery.resourceViewer",
    "bigqueryconnection.googleapis.com": "roles/bigquery.connectionUser (somente list/get) ou papel customizado",
    "storage.googleapis.com": "storage.buckets.list/get + storage.objects.list (papel customizado)",
    "monitoring.googleapis.com": "roles/monitoring.viewer",
    "composer.googleapis.com": "roles/composer.user (UI/REST do Airflow) ou roles/composer.environmentAndStorageObjectViewer",
    "dataflow.googleapis.com": "roles/dataflow.viewer",
    "dataproc.googleapis.com": "roles/dataproc.viewer",
    "dataform.googleapis.com": "roles/dataform.viewer",
    "datafusion.googleapis.com": "roles/datafusion.viewer",
    "pubsub.googleapis.com": "roles/pubsub.viewer",
    "datastream.googleapis.com": "roles/datastream.viewer",
    "cloudscheduler.googleapis.com": "roles/cloudscheduler.viewer",
    "workflows.googleapis.com": "roles/workflows.viewer",
    "cloudfunctions.googleapis.com": "roles/cloudfunctions.viewer",
    "run.googleapis.com": "roles/run.viewer",
    "sqladmin.googleapis.com": "roles/cloudsql.viewer",
    "alloydb.googleapis.com": "roles/alloydb.viewer",
    "spanner.googleapis.com": "roles/spanner.viewer",
    "container.googleapis.com": "roles/container.clusterViewer",
    "aiplatform.googleapis.com": "roles/aiplatform.viewer",
    "dataplex.googleapis.com": "roles/dataplex.viewer",
    "datacatalog.googleapis.com": "roles/datacatalog.viewer",
    "datalineage.googleapis.com": "roles/datalineage.viewer",
    "dlp.googleapis.com": "roles/dlp.dataProfilesReader",
    "accesscontextmanager.googleapis.com": "roles/accesscontextmanager.policyReader (na organização)",
    "orgpolicy.googleapis.com": "roles/orgpolicy.policyViewer",
    "looker.googleapis.com": "roles/looker.viewer",
    "api.powerbi.com": "Service principal habilitado para 'read-only admin APIs' (Fabric admin portal)",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def api_from_url(url: str) -> str:
    host = url.split("//", 1)[-1].split("/", 1)[0]
    if host.endswith("-aiplatform.googleapis.com"):
        return "aiplatform.googleapis.com"
    if host == "www.googleapis.com" or host == "storage.googleapis.com":
        return "storage.googleapis.com"
    return host


@dataclass
class CoverageEntry:
    module: str
    component: str
    status: str = NOT_ASSESSED
    resources_found: int | None = None
    resources_assessed: int | None = None
    missing_permissions: list = field(default_factory=list)
    unavailable_apis: list = field(default_factory=list)
    history_window: str | None = None
    limitations: list = field(default_factory=list)
    source: str = ""
    collected_at: str = field(default_factory=_now)
    confidence: str = "medium"
    scope_errors: int = 0
    scope_ok: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["visibility_note"] = self.visibility_note()
        return d

    def visibility_note(self) -> str:
        if self.status == COMPLETE and (self.resources_found or 0) == 0:
            return "Ausência de recursos confirmada (coleta completa, nada encontrado)"
        if self.status in (PERMISSION_DENIED, NOT_AVAILABLE):
            return "Sem visibilidade: ausência de dados NÃO significa ausência de recursos"
        if self.status == PARTIAL:
            return "Visibilidade parcial: números são limite inferior"
        if self.status == NOT_ASSESSED:
            return "Não avaliado nesta execução"
        return ""


class CoverageTracker:
    def __init__(self):
        self._entries: dict[tuple[str, str], CoverageEntry] = {}
        self._missing: list[dict] = []
        self._errors: list[dict] = []
        self._lock = threading.Lock()

    def entry(self, module: str, component: str, **kw) -> CoverageEntry:
        with self._lock:
            key = (module, component)
            if key not in self._entries:
                self._entries[key] = CoverageEntry(module=module, component=component, **kw)
            else:
                for k, v in kw.items():
                    setattr(self._entries[key], k, v)
            return self._entries[key]

    def ok(self, module: str, component: str, found: int = 0) -> None:
        e = self.entry(module, component)
        with self._lock:
            e.scope_ok += 1
            e.resources_found = (e.resources_found or 0) + found
            e.resources_assessed = (e.resources_assessed or 0) + found

    def fail(self, module: str, component: str, exc: Exception, scope: str = "", permission_hint: str | None = None) -> None:
        """Registra falha sem interromper a execução."""
        from utils.http import ApiDisabled, ApiError, PermissionDenied

        e = self.entry(module, component)
        kind = getattr(exc, "kind", "error")
        url = getattr(exc, "url", "") or ""
        api = api_from_url(url) if url.startswith("http") else ""
        hint = permission_hint or API_ROLE_HINTS.get(api, "")
        rec = {"module": module, "component": component, "scope": scope, "api": api, "kind": kind,
               "message": str(getattr(exc, "message", exc))[:400], "suggested_role": hint, "at": _now()}
        with self._lock:
            self._errors.append(rec)
            if isinstance(exc, ApiError) and exc.status == 404:
                e.scope_ok += 1  # recurso/escopo inexistente: não é falta de visibilidade
                return
            e.scope_errors += 1
            if isinstance(exc, ApiDisabled):
                if api and api not in e.unavailable_apis:
                    e.unavailable_apis.append(api)
                self._missing.append({**rec, "type": "api_not_enabled"})
            elif isinstance(exc, PermissionDenied):
                if hint and hint not in e.missing_permissions:
                    e.missing_permissions.append(hint)
                self._missing.append({**rec, "type": "permission_denied"})
            else:
                note = f"{scope}: {rec['message'][:160]}"
                if note not in e.limitations and len(e.limitations) < 20:
                    e.limitations.append(note)

    def limitation(self, module: str, component: str, text: str) -> None:
        e = self.entry(module, component)
        with self._lock:
            if text not in e.limitations:
                e.limitations.append(text)

    def finalize(self, module: str, component: str, *, applicable: bool = True, source: str | None = None,
                 history_window: str | None = None, confidence: str | None = None) -> CoverageEntry:
        e = self.entry(module, component)
        with self._lock:
            if source:
                e.source = source
            if history_window:
                e.history_window = history_window
            if confidence:
                e.confidence = confidence
            e.collected_at = _now()
            if not applicable:
                e.status = NOT_APPLICABLE
            elif e.scope_ok == 0 and e.scope_errors == 0:
                e.status = e.status if e.status != NOT_ASSESSED else NOT_ASSESSED
            elif e.scope_errors == 0:
                e.status = COMPLETE
            elif e.scope_ok == 0:
                e.status = PERMISSION_DENIED if (e.missing_permissions and not e.unavailable_apis) else (
                    NOT_AVAILABLE if e.unavailable_apis and not e.missing_permissions else
                    PERMISSION_DENIED if e.missing_permissions else PARTIAL)
            else:
                e.status = PARTIAL
            if e.status in (PARTIAL, PERMISSION_DENIED, NOT_AVAILABLE):
                e.confidence = "low" if e.status != PARTIAL else "medium"
        return e

    def reset_module(self, module: str) -> None:
        """Remove entradas de um módulo antes de reexecutá-lo (execuções incrementais no mesmo output)."""
        with self._lock:
            for key in [k for k in self._entries if k[0] == module]:
                del self._entries[key]
            self._missing = [m for m in self._missing if m.get("module") != module]

    # ----------------------------------------------------------- export
    def entries(self) -> list[dict]:
        return [e.to_dict() for e in sorted(self._entries.values(), key=lambda x: (x.module, x.component))]

    def missing(self) -> list[dict]:
        return list(self._missing)

    def errors(self) -> list[dict]:
        return list(self._errors)

    def load(self, entries: list[dict], missing: list[dict]) -> None:
        for d in entries:
            d = {k: v for k, v in d.items() if k in CoverageEntry.__dataclass_fields__}
            self._entries[(d["module"], d["component"])] = CoverageEntry(**d)
        self._missing = list(missing)
