"""Autenticação.

GCP: Application Default Credentials (ADC) ou impersonação de Service Account
(`gcp.impersonate_service_account`). Escopo `cloud-platform` (ver nota em GCP_SCOPES); a garantia de somente leitura vem dos papéis IAM
mínimos e do bloqueio de escrita no cliente HTTP.

Power BI: Microsoft Entra ID, independente do GCP, via MSAL:
  - service principal (client credentials; segredo lido de variável de ambiente)
  - device code (usuário administrador do Fabric, escopo delegado)

Nenhum token é gravado em disco ou em log.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

from utils.http import ApiClient, HttpSettings
from utils.logging import get_logger

log = get_logger("auth")

# O escopo `cloud-platform.read-only` NÃO é aceito por várias APIs (IAM, Cloud Asset, Pub/Sub, Compute, Monitoring...):
# na impersonação de service account o token é emitido só com os escopos pedidos e essas chamadas retornam
# 403 "insufficient authentication scopes" (com credenciais de usuário o escopo é ignorado e o problema não aparece).
# O escopo amplo não concede permissão alguma: o acesso efetivo continua limitado aos papéis IAM da conta (somente
# leitura) e o cliente HTTP da ferramenta bloqueia qualquer método de escrita (utils/http.py).
GCP_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"


class AuthError(Exception):
    pass


@dataclass
class GcpIdentity:
    principal: str
    principal_hash: str
    method: str
    quota_project: str | None


def gcp_client(config: dict, settings: HttpSettings) -> tuple[ApiClient, GcpIdentity]:
    try:
        import google.auth
        from google.auth import impersonated_credentials
        from google.auth.transport.requests import AuthorizedSession, Request
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        raise AuthError("Pacote google-auth não instalado (pip install -r requirements.txt)") from exc

    gcfg = config.get("gcp", {}) or {}
    quota_project = gcfg.get("quota_project")
    try:
        creds, default_project = google.auth.default(scopes=GCP_SCOPES, quota_project_id=quota_project)
    except Exception as exc:
        raise AuthError(f"ADC indisponível: {exc}. Execute 'gcloud auth application-default login' "
                        "ou defina GOOGLE_APPLICATION_CREDENTIALS.") from exc

    method = "adc"
    target = gcfg.get("impersonate_service_account")
    if target:
        creds = impersonated_credentials.Credentials(
            source_credentials=creds, target_principal=target, target_scopes=GCP_SCOPES, lifetime=3600)
        if quota_project:
            creds = creds.with_quota_project(quota_project)
        method = "impersonation"

    try:
        creds.refresh(Request())
    except Exception as exc:
        raise AuthError(f"Falha ao obter token GCP: {exc}") from exc

    principal = target or getattr(creds, "service_account_email", None) or _adc_user_email(creds) or "adc-user"
    session = AuthorizedSession(creds)
    if quota_project:
        session.headers["x-goog-user-project"] = quota_project
    session.headers["User-Agent"] = "gcp-data-platform-assessment/1.0 (read-only)"
    ident = GcpIdentity(principal=principal, principal_hash=_h(principal), method=method,
                        quota_project=quota_project or default_project)
    log.info("GCP autenticado via %s (principal hash %s)", method, ident.principal_hash)
    return ApiClient(session, settings, name="gcp"), ident


def _adc_user_email(creds) -> str | None:
    # Credenciais de usuário (gcloud ADC) não expõem o e-mail diretamente; tentamos o id_token se houver.
    token = getattr(creds, "id_token", None)
    if not token:
        return None
    try:
        import base64
        import json

        payload = token.split(".")[1] + "=="
        return json.loads(base64.urlsafe_b64decode(payload)).get("email")
    except Exception:
        return None


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


# ---------------------------------------------------------------- Power BI
class _BearerSession:
    """Sessão `requests` que injeta um token MSAL e o renova quando expira."""

    def __init__(self, token_provider):
        import requests

        self._s = requests.Session()
        self._s.headers["User-Agent"] = "gcp-data-platform-assessment/1.0 (read-only)"
        self._provider = token_provider

    def request(self, method, url, **kw):
        headers = dict(kw.pop("headers", None) or {})
        headers["Authorization"] = f"Bearer {self._provider()}"
        return self._s.request(method, url, headers=headers, **kw)


def powerbi_client(config: dict, settings: HttpSettings) -> tuple[ApiClient, str]:
    try:
        import msal
    except ImportError as exc:  # pragma: no cover
        raise AuthError("Pacote msal não instalado; use o modo offline do Power BI ou instale msal.") from exc

    pcfg = config.get("powerbi", {}) or {}
    tenant = pcfg.get("tenant_id") or os.environ.get("PBI_TENANT_ID")
    client_id = pcfg.get("client_id") or os.environ.get("PBI_CLIENT_ID")
    mode = (pcfg.get("auth_mode") or "service_principal").lower()
    if not tenant or not client_id:
        raise AuthError("powerbi.tenant_id e powerbi.client_id são obrigatórios (ou PBI_TENANT_ID/PBI_CLIENT_ID).")
    authority = f"https://login.microsoftonline.com/{tenant}"

    if mode == "service_principal":
        secret_env = pcfg.get("client_secret_env", "PBI_CLIENT_SECRET")
        secret = os.environ.get(secret_env)
        cert_path = pcfg.get("certificate_path")
        if cert_path:
            with open(cert_path, encoding="utf-8") as fh:
                credential = {"private_key": fh.read(), "thumbprint": pcfg.get("certificate_thumbprint")}
        elif secret:
            credential = secret
        else:
            raise AuthError(f"Defina a variável de ambiente {secret_env} (segredo do service principal) "
                            "ou powerbi.certificate_path.")
        app = msal.ConfidentialClientApplication(client_id, authority=authority, client_credential=credential)

        def provider() -> str:
            res = app.acquire_token_for_client(scopes=[POWERBI_SCOPE])
            if "access_token" not in res:
                raise AuthError(f"Falha MSAL: {res.get('error')}: {res.get('error_description', '')[:200]}")
            return res["access_token"]
    else:
        app = msal.PublicClientApplication(client_id, authority=authority)
        scopes = ["https://analysis.windows.net/powerbi/api/Tenant.Read.All"]
        state: dict = {}

        def provider() -> str:
            accounts = app.get_accounts()
            if accounts:
                res = app.acquire_token_silent(scopes, account=accounts[0])
                if res and "access_token" in res:
                    return res["access_token"]
            if "flow" not in state:
                flow = app.initiate_device_flow(scopes=scopes)
                if "user_code" not in flow:
                    raise AuthError(f"Device flow falhou: {flow.get('error_description', '')[:200]}")
                print(flow["message"])  # instrução para o usuário (sem segredo)
                state["flow"] = flow
            res = app.acquire_token_by_device_flow(state.pop("flow"))
            if "access_token" not in res:
                raise AuthError(f"Falha MSAL: {res.get('error')}")
            return res["access_token"]

    provider()  # valida credenciais antes de iniciar a coleta
    log.info("Power BI autenticado via %s (tenant hash %s)", mode, _h(tenant))
    return ApiClient(_BearerSession(provider), settings, name="powerbi"), mode
