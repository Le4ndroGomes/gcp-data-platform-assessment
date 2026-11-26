"""Gera docs/SQL_QUERIES.md com todas as consultas SQL emitidas pelos collectors.

Executa os collectors BigQuery/Billing contra um executor falso que apenas
registra o SQL (nenhuma chamada de rede). Os identificadores aparecem como
placeholders (PROJECT_ID, REGION, DATASET).
"""
from __future__ import annotations

import copy
import tempfile
from pathlib import Path

from core.context import Context
from core.coverage import CoverageTracker
from utils.redaction import Redactor

_NESTED = {
    "query_info": {"name": "query_info", "fields": [{"name": "query_hashes", "fields": [{"name": "normalized_literals"}]}]},
    "destination_table": {"name": "destination_table", "fields": [{"name": "project_id"}, {"name": "dataset_id"}, {"name": "table_id"}]},
}


class _PermissiveSchema(dict):
    """Schema que declara qualquer coluna como existente (para listar o SQL completo)."""

    def __init__(self):
        super().__init__({"_": {"name": "_"}})

    def get(self, key, default=None):
        return _NESTED.get(key, {"name": key, "fields": []})

    def __contains__(self, key):
        return True


class _RecordingRunner:
    def __init__(self):
        self.queries: list[tuple[str, str]] = []
        self.stats = None

    def view_schema(self, project, location, view_ref):
        return _PermissiveSchema()

    def query(self, project, location, sql, name="query", max_rows=0, max_bytes=None):
        self.queries.append((name.split(":")[0], sql.strip()))
        if name.startswith("schemata:"):
            return [{"schema_name": "DATASET"}]
        return []


class _NullApi:
    request_count = 0

    def get(self, *a, **k):
        return {}

    def post(self, *a, **k):
        return {}

    def paginate(self, *a, **k):
        return iter(())

    def paginate_aggregated(self, *a, **k):
        return iter(())


def write_query_catalog(path: Path, config: dict) -> Path:
    from collectors import bigquery_metadata, bigquery_workloads, billing

    cfg = copy.deepcopy(config)
    cfg["bigquery"]["collect_partitions"] = True
    cfg["billing"]["export_table"] = "BILLING_PROJECT.BILLING_DATASET.gcp_billing_export_resource_v1_XXXXXX_XXXXXX_XXXXXX"
    sections = []
    for mode in ("features", "full"):
        tmp = Path(tempfile.mkdtemp(prefix="gcpa-q-"))
        cfg["sql"]["mode"] = mode
        ctx = Context(config=cfg, output_dir=tmp, coverage=CoverageTracker(), redactor=Redactor(), days=90, include_sql=(mode == "full"))
        ctx.gcp = _NullApi()
        ctx.bq = _RecordingRunner()
        ctx.projects = [{"project_id": "PROJECT_ID", "selected": True, "bigquery_locations": ["REGION"]}]
        bigquery_metadata.collect(ctx)
        bigquery_workloads.collect(ctx)
        if mode == "features":
            billing.collect(ctx)
        seen = set()
        for name, sql in ctx.bq.queries:
            key = (name, sql)
            if key in seen:
                continue
            seen.add(key)
            sections.append((mode, name, sql))
    lines = ["# Consultas SQL utilizadas no assessment", "",
             "Gerado automaticamente por `python3 main.py --print-queries`. Todas as consultas são `SELECT` sobre visões",
             "`INFORMATION_SCHEMA` (ou sobre a tabela de Billing Export já existente), executadas após *dry run* e com",
             "`maximumBytesBilled`. Colunas ausentes em uma região/versão são substituídas por `CAST(NULL AS ...)` após",
             "validação de schema por dry run (`SELECT *` sem custo). Placeholders: `PROJECT_ID`, `REGION`, `DATASET`.", ""]
    last_mode = None
    for mode, name, sql in sections:
        if mode != last_mode:
            lines += [f"## Modo SQL `{mode}`", "" if mode == "features" else
                      "_Somente com `--include-sql`: o texto SQL é lido para análise com SQLGlot (não é gravado sem `--store-sql`)._", ""]
            last_mode = mode
        lines += [f"### {name}", "", "```sql", sql, "```", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
