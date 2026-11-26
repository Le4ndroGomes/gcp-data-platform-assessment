"""FinOps — Cloud Billing Export para BigQuery (item 10).

Usa somente tabelas de exportação JÁ configuradas pelo cliente
(`gcp_billing_export_v1_*` padrão ou `gcp_billing_export_resource_v1_*` detalhada).
Autodetecção a partir do inventário de tabelas, ou `billing.export_table`.

Custos faturados = SUM(cost) + SUM(credits.amount) (créditos são negativos).
Sem acesso ao export, o analyzer gera apenas ESTIMATIVAS rotuladas como tal.
"""
from __future__ import annotations

import re

from core.context import Context
from utils.bq import CostLimitExceeded
from utils.http import ApiError
from utils.logging import get_logger

log = get_logger("billing")
MODULE = "billing"
EXPORT_RE = re.compile(r"^gcp_billing_export_(resource_)?v1_[0-9A-F_]+$", re.IGNORECASE)


def find_export_tables(ctx: Context) -> list[dict]:
    explicit = ctx.config["billing"].get("export_table")
    found = []
    meta_dir = ctx.raw_dir / "bigquery"
    for f in sorted(meta_dir.glob("metadata__*.json")) if meta_dir.exists() else []:
        data = ctx.load_raw("bigquery", f.stem) or {}
        for t in data.get("tables") or []:
            name = t.get("table_name") or ""
            fq = f"{data['project_id']}.{t.get('table_schema')}.{name}"
            if (explicit and fq == explicit) or (not explicit and EXPORT_RE.match(name)):
                found.append({"table": fq, "project_id": data["project_id"], "location": data["location"],
                              "detailed": "resource_v1" in name.lower()})
    if explicit and not found:
        proj = explicit.split(".")[0]
        loc = ctx.config["billing"].get("export_location") or "US"
        found.append({"table": explicit, "project_id": proj, "location": loc, "detailed": "resource_v1" in explicit.lower()})
    # Preferir a exportação detalhada; uma por billing account (sufixo do nome)
    by_account: dict[str, dict] = {}
    for t in sorted(found, key=lambda x: not x["detailed"]):
        acct = t["table"].rsplit("_v1_", 1)[-1].upper()
        by_account.setdefault(acct, t)
    return list(by_account.values())


def _queries(table: str, months: int, detailed: bool, part_filter: bool) -> dict[str, str]:
    t = f"`{table}`"
    where = (f"usage_start_time >= TIMESTAMP(DATE_SUB(DATE_TRUNC(CURRENT_DATE(), MONTH), INTERVAL {months} MONTH))")
    if part_filter:
        where += f" AND DATE(_PARTITIONTIME) >= DATE_SUB(DATE_TRUNC(CURRENT_DATE(), MONTH), INTERVAL {months} MONTH)"
    credits = "IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)"
    q = {
        "by_service_month": f"""SELECT invoice.month AS month, service.description AS service, SUM(cost) AS cost,
  SUM({credits}) AS credits, ANY_VALUE(currency) AS currency FROM {t} WHERE {where} GROUP BY 1, 2""",
        "by_project_month": f"""SELECT invoice.month AS month, project.id AS project_id, SUM(cost) AS cost,
  SUM({credits}) AS credits FROM {t} WHERE {where} GROUP BY 1, 2""",
        "by_sku": f"""SELECT service.description AS service, sku.description AS sku, SUM(cost) AS cost,
  SUM({credits}) AS credits, SUM(usage.amount) AS usage_amount, ANY_VALUE(usage.unit) AS usage_unit,
  SUM(usage.amount_in_pricing_units) AS pricing_amount, ANY_VALUE(usage.pricing_unit) AS pricing_unit
FROM {t} WHERE {where} GROUP BY 1, 2 ORDER BY cost DESC LIMIT 1000""",
        "by_sku_month": f"""SELECT invoice.month AS month, service.description AS service, sku.description AS sku,
  SUM(cost) AS cost, SUM({credits}) AS credits FROM {t} WHERE {where}
  AND (service.description LIKE '%BigQuery%' OR LOWER(sku.description) LIKE '%egress%'
       OR LOWER(sku.description) LIKE '%network%' OR service.description IN ('Cloud Storage', 'Cloud Composer',
       'Dataflow', 'Cloud Dataproc', 'Dataproc', 'Datastream', 'Cloud Pub/Sub', 'Cloud Data Fusion', 'Dataform',
       'Vertex AI', 'Looker', 'Networking', 'Compute Engine', 'Cloud Run', 'Cloud Functions', 'Cloud SQL'))
GROUP BY 1, 2, 3""",
        "credits_by_type": f"""SELECT invoice.month AS month, c.type AS credit_type, SUM(c.amount) AS amount
FROM {t}, UNNEST(credits) c WHERE {where} GROUP BY 1, 2""",
        "cost_type": f"""SELECT invoice.month AS month, IFNULL(cost_type, 'regular') AS cost_type, SUM(cost) AS cost
FROM {t} WHERE {where} GROUP BY 1, 2""",
        "by_region_month": f"""SELECT invoice.month AS month, IFNULL(location.region, location.location) AS region,
  SUM(cost) AS cost FROM {t} WHERE {where} GROUP BY 1, 2""",
    }
    if detailed:
        q["top_resources"] = f"""SELECT service.description AS service, resource.name AS resource_name,
  project.id AS project_id, SUM(cost) AS cost, SUM({credits}) AS credits
FROM {t} WHERE {where} AND usage_start_time >= TIMESTAMP(DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY))
  AND resource.name IS NOT NULL GROUP BY 1, 2, 3 ORDER BY cost DESC LIMIT 300"""
    return q


def collect(ctx: Context) -> dict:
    exports = find_export_tables(ctx)
    months = int(ctx.config["billing"].get("months", 12))
    cap = int(ctx.config["billing"].get("max_bytes_billed_per_query", 10 * 1024 ** 3))
    if not exports:
        ctx.coverage.entry(MODULE, "billing_export").status = "Not Available"
        ctx.coverage.limitation(MODULE, "billing_export",
                                "Nenhuma tabela de Billing Export encontrada/acessível. Custos serão apenas ESTIMATIVAS.")
        ctx.save_raw(MODULE, "billing_export", {"exports": [], "results": {}})
        return {"exports": 0}
    results: dict = {}
    for e in exports:
        res = {}
        part_filter = True
        for name, sql in _queries(e["table"], months, e["detailed"], part_filter).items():
            try:
                rows = ctx.bq.query(e["project_id"], e["location"], sql, name=f"billing:{name}", max_bytes=cap)
            except ApiError as exc:
                if part_filter and exc.status == 400 and "_PARTITIONTIME" in (exc.message or ""):
                    part_filter = False
                    sql = _queries(e["table"], months, e["detailed"], False)[name]
                    try:
                        rows = ctx.bq.query(e["project_id"], e["location"], sql, name=f"billing:{name}", max_bytes=cap)
                    except (ApiError, CostLimitExceeded) as exc2:
                        _fail(ctx, exc2, e["table"])
                        continue
                else:
                    _fail(ctx, exc, e["table"])
                    continue
            except CostLimitExceeded as exc:
                _fail(ctx, exc, e["table"])
                continue
            res[name] = rows
            ctx.coverage.ok(MODULE, "billing_export", len(rows))
        for r in res.get("top_resources") or []:
            r["resource_name"] = ctx.redactor.text(r.get("resource_name"))
        results[e["table"]] = res
    ctx.save_raw(MODULE, "billing_export", {"exports": exports, "results": results, "months": months})
    ctx.coverage.finalize(MODULE, "billing_export", source="Cloud Billing Export (BigQuery)",
                          history_window=f"{months} meses", confidence="high")
    return {"exports": len(exports)}


def _fail(ctx: Context, exc: Exception, table: str) -> None:
    if isinstance(exc, CostLimitExceeded):
        ctx.coverage.limitation(MODULE, "billing_export", str(exc))
        ctx.coverage.entry(MODULE, "billing_export").scope_errors += 1
    else:
        ctx.coverage.fail(MODULE, "billing_export", exc, scope=table,
                          permission_hint="roles/bigquery.dataViewer no dataset do Billing Export + roles/bigquery.jobUser")
