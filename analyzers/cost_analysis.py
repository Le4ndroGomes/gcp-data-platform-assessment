"""FinOps e custos (item 10).

Três categorias mantidas SEPARADAS no relatório:
  1. billed     — Cloud Billing Export (custo faturado = cost + créditos)
  2. estimated  — estimativas a partir de métricas x preços de lista configuráveis
                  (`pricing_estimates`), sempre rotuladas como ESTIMATIVA
  3. metrics    — consumo bruto (bytes, slot-hours) sem conversão monetária
Anti-dupla contagem: bytes on-demand consideram apenas jobs sem reservation_id;
slots reservados entram somente como custo de capacidade da reserva.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date

from utils.fmt import b as fmt_bytes, n as fmt_n

TiB, GiB = 1024 ** 4, 1024 ** 3
CATEGORY_RULES = [
    ("rede/egress", r"egress|network|interconnect|vpn|nat|load balanc|cdn|data transfer"),
    ("armazenamento", r"storage|long term|active logical|active physical|snapshot|backup|archive|nearline|coldline"),
    ("ingestão", r"streaming insert|storage write api|datastream|pub/sub|pubsub|transfer service|data fusion"),
    ("orquestração", r"composer|workflows|scheduler|dataform"),
    ("processamento", r"analysis|slot|edition|reservation|compute|vcpu|core|ram|dataflow|dataproc|run|functions|gpu"),
    ("BI", r"looker|bi engine"),
    ("IA/ML", r"vertex|prediction|training|gemini|ai platform|automl"),
]


def categorize(service: str, sku: str) -> str:
    text = f"{service} {sku}".lower()
    for cat, rx in CATEGORY_RULES:
        if re.search(rx, text):
            return cat
    return "outros"


def _last_full_months(months: list[str], n: int) -> list[str]:
    today = date.today()
    current = f"{today.year}{today.month:02d}"
    full = sorted(m for m in months if m and m < current)
    return full[-n:]


def analyze(inv: dict, config: dict, workloads: dict, volume: dict) -> dict:
    billing = inv.get("billing") or {}
    results = billing.get("results") or {}
    out = {"billed": None, "estimated": {}, "metrics": {}, "underutilization": [], "notes": []}

    # ------------------------------------------------------------- billed
    if results:
        by_month, by_service, by_project, by_sku, cats = Counter(), defaultdict(Counter), defaultdict(Counter), [], defaultdict(Counter)
        credits_type = defaultdict(Counter)
        currency = None
        bq_detail = defaultdict(Counter)
        for table, res in results.items():
            for r in res.get("by_service_month") or []:
                net = (r.get("cost") or 0) + (r.get("credits") or 0)
                by_month[r["month"]] += net
                by_service[r["service"]][r["month"]] += net
                currency = currency or r.get("currency")
            for r in res.get("by_project_month") or []:
                by_project[r.get("project_id") or "(sem projeto)"][r["month"]] += (r.get("cost") or 0) + (r.get("credits") or 0)
            for r in res.get("by_sku") or []:
                cat = categorize(r.get("service", ""), r.get("sku", ""))
                by_sku.append({**r, "net": (r.get("cost") or 0) + (r.get("credits") or 0), "category": cat})
            for r in res.get("by_sku_month") or []:
                cats[categorize(r.get("service", ""), r.get("sku", ""))][r["month"]] += (r.get("cost") or 0) + (r.get("credits") or 0)
                if "BigQuery" in (r.get("service") or ""):
                    bq_detail[_bq_bucket(r.get("sku", ""))][r["month"]] += (r.get("cost") or 0) + (r.get("credits") or 0)
            for r in res.get("credits_by_type") or []:
                credits_type[r.get("credit_type") or "?"][r["month"]] += r.get("amount") or 0
        months = sorted(by_month)
        full = _last_full_months(months, 12)
        last = full[-1] if full else None

        def window(n):
            ms = full[-n:]
            return {"months": ms, "total": round(sum(by_month[m] for m in ms), 2),
                    "avg_month": round(sum(by_month[m] for m in ms) / len(ms), 2) if ms else None} if len(ms) >= 1 else None

        evol = []
        for i, m in enumerate(months):
            prev = by_month[months[i - 1]] if i else None
            evol.append({"month": m, "net_cost": round(by_month[m], 2),
                         "mom_pct": round(100 * (by_month[m] - prev) / prev, 1) if prev else None, "partial": m not in full})
        drivers = sorted(by_sku, key=lambda r: -r["net"])[:15]
        out["billed"] = {
            "currency": currency, "months": months, "last_full_month": last,
            "monthly_total_last_full": round(by_month[last], 2) if last else None,
            "window_3m": window(3), "window_6m": window(6), "window_12m": window(12),
            "evolution": evol,
            "by_service_last_month": sorted([{"service": s, "net_cost": round(v.get(last, 0), 2)} for s, v in by_service.items()],
                                            key=lambda x: -x["net_cost"])[:25] if last else [],
            "by_project_last_month": sorted([{"project_id": p, "net_cost": round(v.get(last, 0), 2)} for p, v in by_project.items()],
                                            key=lambda x: -x["net_cost"])[:25] if last else [],
            "by_category_last_month": {c: round(v.get(last, 0), 2) for c, v in cats.items()} if last else {},
            "bigquery_breakdown_last_month": {k: round(v.get(last, 0), 2) for k, v in bq_detail.items()} if last else {},
            "credits_by_type_window": {k: round(sum(v.values()), 2) for k, v in credits_type.items()},
            "top_cost_drivers": [{"service": d["service"], "sku": d["sku"], "net_cost_window": round(d["net"], 2),
                                  "category": d["category"]} for d in drivers],
            "top_resources_30d": [r for res in results.values() for r in res.get("top_resources") or []][:50],
            "source": ", ".join(results.keys()),
        }
    else:
        out["notes"].append("Billing Export indisponível: valores monetários abaixo são ESTIMATIVAS baseadas em preços de lista.")

    # ---------------------------------------------------------- estimated
    pe = config.get("pricing_estimates", {})
    cur = pe.get("currency", "USD")
    t = workloads.get("totals") or {}
    window_days = max(workloads.get("window_days") or 30, 1)
    on_demand_tib_month = (t.get("on_demand_bytes_billed") or 0) / TiB * 30 / window_days
    est = {"currency": cur, "label": "ESTIMATIVA (preços de lista; não é custo faturado)", "source": pe.get("source")}
    if workloads.get("available"):
        est["bq_on_demand_analysis_month"] = round(on_demand_tib_month * pe.get("bq_on_demand_per_tib", 6.25), 2)
        est["bq_on_demand_tib_month"] = round(on_demand_tib_month, 3)
    physical_datasets = {d["ref"] for d in inv.get("datasets") or [] if str((d.get("options") or {}).get("storage_billing_model", "")).upper().strip('"') == "PHYSICAL"}
    stor = Counter()
    for tb in inv.get("tables_by_ref", {}).values():
        if tb.get("deleted") or tb.get("table_type") in ("VIEW", "EXTERNAL"):
            continue
        ds = f"{tb['project_id']}.{tb['dataset']}"
        if ds in physical_datasets:
            stor["phys_active"] += tb.get("active_physical_bytes") or 0
            stor["phys_lt"] += tb.get("long_term_physical_bytes") or 0
        else:
            stor["log_active"] += tb.get("active_logical_bytes") or 0
            stor["log_lt"] += tb.get("long_term_logical_bytes") or 0
    est["bq_storage_month"] = round(stor["log_active"] / GiB * pe.get("bq_active_logical_per_gib_month", 0.02)
                                    + stor["log_lt"] / GiB * pe.get("bq_long_term_logical_per_gib_month", 0.01)
                                    + stor["phys_active"] / GiB * pe.get("bq_active_physical_per_gib_month", 0.04)
                                    + stor["phys_lt"] / GiB * pe.get("bq_long_term_physical_per_gib_month", 0.02), 2)
    est["bq_storage_billing_model"] = {"physical_datasets": len(physical_datasets)}
    slot_prices = pe.get("bq_edition_slot_hour", {"STANDARD": 0.04, "ENTERPRISE": 0.06, "ENTERPRISE_PLUS": 0.10})
    baseline_cost = 0.0
    baseline_slots = Counter()
    for c in inv.get("capacity") or []:
        for r in c.get("reservations") or []:
            ed = (r.get("edition") or "ENTERPRISE").upper()
            baseline_slots[ed] += r.get("slot_capacity") or 0
            baseline_cost += (r.get("slot_capacity") or 0) * 730 * slot_prices.get(ed, 0.06)
    if baseline_slots:
        est["bq_reservation_baseline_month"] = round(baseline_cost, 2)
        est["bq_reservation_baseline_slots"] = dict(baseline_slots)
        est["notes_reservation"] = "Somente slots baseline (24x7); autoscale e commitments com desconto não estimados."
    gcs = 0.0
    prices = pe.get("gcs_per_gib_month", {})
    for b in inv.get("buckets") or []:
        for sc, v in (b.get("bytes_by_class") or {}).items():
            gcs += v / GiB * prices.get(sc.upper(), prices.get("STANDARD", 0.02))
    if inv.get("buckets"):
        est["gcs_storage_month"] = round(gcs, 2)
    out["estimated"] = est

    # ------------------------------------------------------------ metrics
    out["metrics"] = {"bytes_billed_window": t.get("bytes_billed"), "on_demand_bytes_billed_window": t.get("on_demand_bytes_billed"),
                      "slot_hours_window": (workloads.get("slots") or {}).get("slot_hours"), "window_days": workloads.get("window_days"),
                      "bq_logical_bytes": (volume.get("logical") or {}).get("bytes"),
                      "bq_physical_bytes": (volume.get("physical") or {}).get("bytes"),
                      "gcs_bytes": ((volume.get("gcs") or {}).get("total") or {}).get("bytes")}

    # ---------------------------------------------------- subutilização
    avg_slots = (workloads.get("slots") or {}).get("avg_slots_window")
    total_baseline = sum(baseline_slots.values())
    if total_baseline and avg_slots is not None and avg_slots < 0.4 * total_baseline:
        out["underutilization"].append({"resource": "BigQuery reservations", "evidence":
                                        f"Média de {fmt_n(avg_slots, 1)} slots usados vs {fmt_n(total_baseline)} slots baseline (<40%)",
                                        "confidence": "medium"})
    for pid, envs in (inv.get("composer") or {}).items():
        for e in envs or []:
            runs = sum(1 for d in e.get("dags") or [] if not d.get("is_paused"))
            if e.get("state") == "RUNNING" and runs == 0:
                out["underutilization"].append({"resource": f"Composer {e['name'].split('/')[-1]}",
                                                "evidence": "Ambiente RUNNING sem DAGs ativas observadas", "confidence": "medium"})
    dataproc_runs = Counter(p["project_id"] for p in inv.get("pipelines") or [] if p["technology"].startswith("Dataproc"))
    for pid, items in (inv.get("dataproc") or {}).items():
        for c in items or []:
            if c.get("type") == "cluster" and c.get("state") == "RUNNING" and not dataproc_runs.get(pid):
                out["underutilization"].append({"resource": f"Dataproc {c['name']}", "evidence":
                                                "Cluster RUNNING sem jobs observados na janela", "confidence": "medium"})
    lt = (volume.get("long_term_logical") or {}).get("bytes") or 0
    lg = (volume.get("logical") or {}).get("bytes") or 0
    if lg and lt / lg > 0.6:
        out["underutilization"].append({"resource": "BigQuery storage", "evidence":
                                        f"{round(100 * lt / lg)}% do volume lógico em long-term storage (sem modificação há 90+ dias)",
                                        "confidence": "high"})
    low = volume.get("low_usage") or {}
    if low.get("available") and low.get("count"):
        out["underutilization"].append({"resource": "Tabelas BigQuery", "evidence":
                                        f"{fmt_n(low['count'])} tabelas ({fmt_bytes(low['bytes'])}) sem evidência de uso na janela",
                                        "confidence": "medium"})
    for b in inv.get("buckets") or []:
        if (b.get("total_bytes") or 0) > 1e12 and not b.get("lifecycle_rules") and b.get("storage_class") == "STANDARD":
            out["underutilization"].append({"resource": f"Bucket {b['bucket']}", "evidence":
                                            "> 1 TB em STANDARD sem lifecycle", "confidence": "medium"})
    return out


def _bq_bucket(sku: str) -> str:
    s = sku.lower()
    if "analysis" in s:
        return "on-demand (analysis)"
    if "edition" in s or "slot" in s or "commit" in s:
        return "editions/slots"
    if "streaming" in s or "write api" in s:
        return "ingestão (streaming/Write API)"
    if "storage" in s or "long term" in s:
        return "armazenamento"
    if "bi engine" in s:
        return "BI Engine"
    return "outros"
