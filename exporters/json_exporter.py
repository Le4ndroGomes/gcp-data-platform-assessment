"""Exportação JSON / GraphML / Mermaid e manifesto de coleta (auditabilidade)."""
from __future__ import annotations

import hashlib
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from collectors.lineage import to_graphml, to_mermaid
from core.context import dump_json

TOOL_VERSION = "1.0.0"


def write_all(out: Path, a: dict) -> list[Path]:
    files = []
    g = a["graph"]
    dump_json(out / "data_lineage.json", {"metrics": a["lineage"], **g.to_dict(),
                                          "status_legend": {"confirmed": "execução/configuração explícita/Lineage API",
                                                            "inferred": "heurística", "unknown": "não resolvido"}})
    (out / "data_lineage.graphml").write_text(to_graphml(g), encoding="utf-8")
    (out / "data_lineage.mmd").write_text(to_mermaid(g), encoding="utf-8")
    (out / "architecture_as_is.mmd").write_text(a["architecture_mermaid"], encoding="utf-8")
    dump_json(out / "migration_readiness.json", a["readiness"])
    dump_json(out / "databricks_sizing_inputs.json", a["sizing"])
    dump_json(out / "assessment_coverage.json", a["coverage"])
    dump_json(out / "missing_permissions.json", a["missing"])
    if a.get("focus"):
        dump_json(out / "use_case_assessment.json", a["focus"])
    dump_json(out / "poc_candidates.json", a["poc_candidates"])
    files += [out / f for f in ("data_lineage.json", "data_lineage.graphml", "data_lineage.mmd", "architecture_as_is.mmd",
                                "migration_readiness.json", "databricks_sizing_inputs.json", "assessment_coverage.json",
                                "missing_permissions.json", "poc_candidates.json")]
    return files


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(out: Path, ctx, started: datetime, modules: list[str], extra: dict | None = None) -> Path:
    files = sorted(p for p in out.rglob("*") if p.is_file() and p.name != "collection_manifest.json")
    bq = ctx.bq.stats if ctx.bq is not None else None
    manifest = {
        "tool": "gcp-data-platform-assessment", "version": TOOL_VERSION,
        "started_at": started.isoformat(timespec="seconds"), "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": sys.version.split()[0], "platform": platform.platform(),
        "mode": ctx.args.get("mode"), "modules": modules, "days": ctx.days, "include_sql": ctx.include_sql,
        "anonymize": ctx.redactor.anonymize, "arguments": {k: v for k, v in ctx.args.items() if k not in ("secret",)},
        "identity": {"principal_hash": getattr(ctx.identity, "principal_hash", None), "method": getattr(ctx.identity, "method", None)},
        "projects_selected": ctx.project_ids() if ctx.projects else [],
        "bigquery_query_stats": None if bq is None else {"queries": bq.queries, "dry_runs": bq.dry_runs, "skipped_cost_limit": bq.skipped_cost,
                                                         "bytes_estimated": bq.bytes_estimated, "bytes_billed": bq.bytes_billed,
                                                         "failures": bq.failures},
        "http_requests": {"gcp": getattr(ctx.gcp, "request_count", None), "powerbi": getattr(ctx.powerbi, "request_count", None)},
        "read_only_guarantees": ["Somente GET; POST apenas em endpoints de leitura allowlisted (consultas SELECT, dry run, "
                                 "testIamPermissions, getIamPolicy, searchLinks, scanner Power BI)",
                                 "Nenhuma tabela de negócio consultada; nenhum recurso criado ou alterado",
                                 "Nenhuma informação enviada a serviços externos além das APIs Google/Microsoft consultadas"],
        "files": [{"path": str(p.relative_to(out)), "bytes": p.stat().st_size, "sha256": _sha(p)} for p in files],
        **(extra or {}),
    }
    if bq is not None:
        dump_json(out / "raw" / "_bigquery_query_log.json", bq.log)
    dump_json(out / "collection_manifest.json", manifest)
    return out / "collection_manifest.json"
