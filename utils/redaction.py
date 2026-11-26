"""Redaction e anonimização.

- `anonymize=True` (padrão): e-mails, principals e nomes de usuários viram
  pseudônimos estáveis (HMAC-SHA256 com salt local), preservando a capacidade
  de agregação sem expor identidades.
- Segredos (senhas, tokens, chaves, connection strings) são sempre removidos,
  independentemente da configuração.
- Hostnames/IPs de origens externas podem ser mascarados (`redact_hosts`).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from typing import Any

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
SECRET_KEY_RE = re.compile(
    r"(?i)^(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret|"
    r"credential|credentials|connection[_-]?string|sas|auth|authorization)$")
INLINE_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|accountkey|sharedaccesssignature)\s*[=:]\s*[^;\s,'\"]+")
SA_DOMAINS = (".iam.gserviceaccount.com", ".gserviceaccount.com")


class Redactor:
    def __init__(self, anonymize: bool = True, redact_hosts: bool = True, salt: str | None = None):
        self.anonymize = anonymize
        self.redact_hosts = redact_hosts
        self._salt = (salt or os.environ.get("GCPA_REDACTION_SALT") or "gcpa-local-salt").encode()

    # ---------------------------------------------------------------- tokens
    def pseudonym(self, value: str | None, prefix: str = "id") -> str | None:
        if value is None or value == "":
            return value
        digest = hmac.new(self._salt, str(value).lower().encode(), hashlib.sha256).hexdigest()[:10]
        return f"{prefix}_{digest}"

    def principal(self, value: str | None) -> str | None:
        """Anonimiza um principal. Service accounts mantêm o tipo e o domínio técnico."""
        if not value or not self.anonymize:
            return value
        v = str(value)
        kind = "user"
        if v.startswith(("serviceAccount:", "user:", "group:", "domain:")):
            kind, v = v.split(":", 1)
        if v in ("allUsers", "allAuthenticatedUsers"):
            return v
        if v.endswith(SA_DOMAINS):
            local, domain = v.split("@", 1)
            # Agentes de serviço do Google (service-123@gcp-sa-*.iam...) não identificam pessoas.
            if domain.startswith("gcp-sa-") or local.startswith("service-") or domain.endswith("cloudservices.gserviceaccount.com"):
                return f"serviceAccount:{self.pseudonym(local, 'sa')}@{domain}"
            return f"serviceAccount:{self.pseudonym(v, 'sa')}"
        if "@" in v:
            domain = v.split("@", 1)[1]
            return f"{kind}:{self.pseudonym(v, kind)}@{domain}"
        return f"{kind}:{self.pseudonym(v, kind)}"

    def email_domain(self, value: str | None) -> str | None:
        if not value or "@" not in value:
            return None
        return value.split("@", 1)[1].lower()

    def text(self, value: str | None) -> str | None:
        """Remove segredos inline e, se anonimizado, e-mails e IPs de um texto livre."""
        if value is None:
            return None
        out = INLINE_SECRET_RE.sub(lambda m: f"{m.group(1)}=***", str(value))
        if self.anonymize:
            out = EMAIL_RE.sub(lambda m: self.principal(m.group(0)) or "", out)
        if self.redact_hosts:
            out = IPV4_RE.sub("x.x.x.x", out)
        return out

    def host(self, value: str | None) -> str | None:
        if not value or not self.redact_hosts:
            return value
        return self.pseudonym(value, "host")

    # ------------------------------------------------------------- estruturas
    def scrub_secrets(self, obj: Any) -> Any:
        """Remove valores de chaves sensíveis em estruturas aninhadas (sempre aplicado)."""
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if isinstance(k, str) and SECRET_KEY_RE.match(k.replace(" ", "")):
                    out[k] = "***" if v not in (None, "") else v
                else:
                    out[k] = self.scrub_secrets(v)
            return out
        if isinstance(obj, list):
            return [self.scrub_secrets(v) for v in obj]
        if isinstance(obj, str):
            return INLINE_SECRET_RE.sub(lambda m: f"{m.group(1)}=***", obj)
        return obj
