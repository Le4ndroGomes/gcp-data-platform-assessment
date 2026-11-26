"""Logging configurado para console + arquivo (output/execution.log).

Nunca registre tokens, credenciais ou valores de parâmetros sensíveis. O filtro
`SecretFilter` aplica uma última barreira de redaction sobre as mensagens.
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

_SECRET_RE = re.compile(
    r"(?i)(bearer\s+[a-z0-9\-._~+/]+=*|"
    r"(access_token|refresh_token|id_token|client_secret|password|passwd|pwd|secret|api[_-]?key)"
    r"(\"?\s*[:=]\s*\"?)[^\s\",;]+)"
)


def _scrub(text: str) -> str:
    def repl(m: re.Match) -> str:
        if m.group(0).lower().startswith("bearer"):
            return "Bearer ***"
        return f"{m.group(2)}{m.group(3)}***"

    return _SECRET_RE.sub(repl, text)


class SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - mensagem malformada
            return True
        scrubbed = _scrub(msg)
        if scrubbed != msg:
            record.msg = scrubbed
            record.args = ()
        return True


def setup_logging(output_dir: Path, level: str = "INFO") -> logging.Logger:
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("gcpa")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s", "%Y-%m-%dT%H:%M:%S")
    fh = logging.FileHandler(output_dir / "execution.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    fh.addFilter(SecretFilter())
    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(getattr(logging, level.upper(), logging.INFO))
    ch.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    ch.addFilter(SecretFilter())
    logger.addHandler(fh)
    logger.addHandler(ch)

    # Bibliotecas de terceiros: apenas avisos.
    for noisy in ("urllib3", "google", "msal", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"gcpa.{name}")
