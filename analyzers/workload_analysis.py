"""Workload analysis e performance (item 6).

Combina as agregações por região/projeto. Percentis globais são estimados a partir
do histograma de durações (buckets fixos, mesclável entre regiões); os percentis
exatos (APPROX_QUANTILES) por região/tipo também são reportados.
Classificação de workloads por principal com regras transparentes (config
`workload_rules`) e nível de confiança.
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone

from collectors.bigquery_workloads import DURATION_BUCKETS_MS

CLASSES = ["Data Engineering e ETL", "Data Warehousing", "Business Intelligence", "Ad hoc Analytics", "Data Science",
           "Machine Learning", "Aplicações operacionais", "Outros ou não classificados"]
DOW = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


def _tz(name: str | None):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name or "UTC")
    except Exception:
        return timezone.utc


def _ts(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None


def percentile_from_hist(hist: Counter, q: float) -> float | None:
    total = sum(hist.values())
    if not total:
        return None
    bounds = [0] + DURATION_BUCKETS_MS
    target = q * total
    acc = 0
    for idx in range(len(bounds)):
        c = hist.get(idx, 0)
        if acc + c >= target and c:
            lo = bounds[idx]
            hi = bounds[idx + 1] if idx + 1 < len(bounds) else bounds[-1] * 1.5
            return round(lo + (hi - lo) * ((target - acc) / c), 1)
        acc += c
    return float(bounds[-1])


def classify_principal(p: dict, labels_by_principal: dict, rules: dict) -> tuple[str, str, str]:
    """Retorna (classe, confiança, motivo)."""
    jobs = p.get("jobs") or 0
    keys = {kv.split("=", 1)[0] for kv in labels_by_principal.get(p.get("principal"), [])}
    etl_keys = set(rules.get("etl_label_keys") or [])
    bi_keys = set(rules.get("bi_label_keys") or [])
    sa = p.get("is_service_account")
    writes = p.get("write_statements") or 0
    selects = p.get("select_statements") or 0
    if p.get("ml_statements"):
        return "Machine Learning", "high", "statements CREATE_MODEL/EXPORT_MODEL"
    if p.get("matches_ds"):
        return "Data Science", "medium", "principal corresponde a padrões de DS (workload_rules.ds_principals)"
    if p.get("matches_bi") or keys & bi_keys:
        return "Business Intelligence", "medium" if not keys & bi_keys else "high", "principal/label de ferramenta de BI"
    if keys & etl_keys:
        return "Data Engineering e ETL", "high", f"labels de orquestração ({', '.join(sorted(keys & etl_keys))})"
    if (p.get("load_jobs") or 0) > 0 or p.get("matches_etl"):
        return "Data Engineering e ETL", "medium", "jobs LOAD ou principal de pipeline"
    if writes and writes >= 0.3 * max(jobs, 1):
        return "Data Warehousing", "medium", "predomínio de transformações SQL (INSERT/MERGE/CTAS)"
    days = max(p.get("active_days") or 1, 1)
    bytes_per_job = (p.get("bytes_billed") or 0) / max(p.get("query_jobs") or 1, 1)
    if sa and selects and jobs / days > 500 and bytes_per_job < 100 * 1024 ** 2:
        return "Aplicações operacionais", "low", "SA com alta frequência de SELECT pequenos"
    if sa and selects and (p.get("active_hours") or 0) >= 8 and (p.get("distinct_fingerprints") or 0) <= max(20, jobs * 0.05):
        return "Business Intelligence", "low", "SA com consultas recorrentes distribuídas no dia (padrão de BI)"
    if not sa and jobs:
        return "Ad hoc Analytics", "medium", "usuário humano"
    return "Outros ou não classificados", "low", "evidência insuficiente"


def analyze(inv: dict, config: dict) -> dict:
    wl = inv.get("workloads") or []
    tz = _tz(config.get("execution", {}).get("timezone"))
    rules = config.get("workload_rules", {})
    if not wl:
        return {"available": False}
    tot = Counter()
    by_type = defaultdict(Counter)
    daily = defaultdict(Counter)
    hour_profile = defaultdict(Counter)
    dow_profile = defaultdict(Counter)
    heat = defaultdict(int)
    for w in wl:
        for h in w.get("hourly") or []:
            for k in ("jobs", "statements", "scripts", "child_jobs", "failed", "cache_hits", "bytes_processed", "bytes_billed",
                      "on_demand_bytes_billed", "slot_ms", "sum_run_ms", "sum_queue_ms"):
                tot[k] += h.get(k) or 0
                by_type[(h.get("job_type"), h.get("priority"))][k] += h.get(k) or 0
            tot["max_run_ms"] = max(tot["max_run_ms"], h.get("max_run_ms") or 0)
            ts = _ts(h.get("hour"))
            if ts:
                lt = ts.astimezone(tz)
                d = lt.date().isoformat()
                daily[d]["jobs"] += h.get("jobs") or 0
                daily[d]["bytes_billed"] += h.get("bytes_billed") or 0
                daily[d]["slot_ms"] += h.get("slot_ms") or 0
                if h.get("job_type") == "QUERY":
                    daily[d]["queries"] += h.get("jobs") or 0
                if h.get("job_type") == "LOAD":
                    daily[d]["loads"] += h.get("jobs") or 0
                hour_profile[lt.hour]["jobs"] += h.get("jobs") or 0
                hour_profile[lt.hour]["slot_ms"] += h.get("slot_ms") or 0
                dow_profile[lt.weekday()]["jobs"] += h.get("jobs") or 0
                heat[(lt.weekday(), lt.hour)] += h.get("jobs") or 0
    days = max(len(daily), 1)
    window = max((w.get("window_days") or 0) for w in wl)

    # Percentis
    hist_run, hist_queue = Counter(), Counter()
    for w in wl:
        for b in w.get("duration_histogram") or []:
            hist_run[b.get("run_bucket")] += b.get("jobs") or 0
            hist_queue[b.get("queue_bucket")] += b.get("jobs") or 0
    percentiles = {f"p{int(q * 100)}_ms": percentile_from_hist(hist_run, q) for q in (0.5, 0.9, 0.95, 0.99)}
    percentiles.update({f"queue_p{int(q * 100)}_ms": percentile_from_hist(hist_queue, q) for q in (0.5, 0.95)})
    exact = [{"project_id": w["project_id"], "location": w["location"], **p} for w in wl for p in w.get("percentiles") or []]

    # Slots e concorrência
    hourly_slots = []
    for w in wl:
        hourly_slots.extend(w.get("timeline") or [])
    slot_by_hour = defaultdict(lambda: {"avg_slots": 0.0, "peak": 0.0, "conc": 0})
    for h in hourly_slots:
        s = slot_by_hour[h.get("hour")]
        s["avg_slots"] += h.get("avg_slots") or 0
        s["peak"] = max(s["peak"], h.get("peak_minute_slots") or 0)  # pico por região (soma seria superestimada)
        s["conc"] += h.get("peak_concurrent_jobs") or 0
    hourly_avg = [v["avg_slots"] for v in slot_by_hour.values()]
    slot_profile = defaultdict(list)
    for hour, v in slot_by_hour.items():
        ts = _ts(hour)
        if ts:
            slot_profile[ts.astimezone(tz).hour].append(v["avg_slots"])
    window_hours = max(window * 24, 1)
    slots = {
        "avg_slots_window": round(tot["slot_ms"] / (window_hours * 3600 * 1000), 2),
        "avg_slots_active_hours": round(statistics.mean(hourly_avg), 2) if hourly_avg else None,
        "p95_hourly_avg_slots": round(sorted(hourly_avg)[int(0.95 * (len(hourly_avg) - 1))], 2) if hourly_avg else None,
        "peak_minute_slots": round(max((v["peak"] for v in slot_by_hour.values()), default=0), 1) or None,
        "peak_concurrent_jobs": max((v["conc"] for v in slot_by_hour.values()), default=0) or None,
        "avg_concurrent_jobs_active_minutes": round(statistics.mean([h.get("avg_concurrent_jobs_active_minutes") or 0
                                                                     for h in hourly_slots]), 2) if hourly_slots else None,
        "slot_hours": round(tot["slot_ms"] / 3.6e6, 1),
        "profile_by_hour": {h: round(statistics.mean(v), 2) for h, v in sorted(slot_profile.items())},
        "source": "JOBS_TIMELINE_BY_PROJECT (por minuto)" if hourly_slots else "indisponível",
        "note": "Concorrência = jobs ativos no minuto (limite superior). Pico por região, não somado entre regiões.",
    }

    # Top queries e recorrência
    fps = [dict(f, project_id=w["project_id"], location=w["location"]) for w in wl for f in w.get("fingerprints") or []]
    fs = Counter()
    for w in wl:
        for s in w.get("fingerprint_summary") or []:
            for k, v in s.items():
                fs[k] += v or 0

    def top(key, n=20):
        return sorted(fps, key=lambda f: -(f.get(key) or 0))[:n]

    # Consumidores e classificação
    labels_by_principal = defaultdict(list)
    for w in wl:
        for lb in w.get("labels") or []:
            labels_by_principal[lb.get("principal")].append(lb.get("kv") or "")
    merged: dict[str, dict] = {}
    sum_keys = ("jobs", "query_jobs", "load_jobs", "extract_copy_jobs", "write_statements", "ml_statements", "select_statements",
                "bytes_billed", "slot_ms", "cache_hits")
    for w in wl:
        for p in w.get("principals") or []:
            m = merged.setdefault(p.get("principal"), {**{k: 0 for k in sum_keys}, "principal": p.get("principal"), "projects": set(),
                                                       "distinct_fingerprints": 0, "active_hours": 0, "active_days": 0})
            for k in sum_keys:
                m[k] += p.get(k) or 0
            for k in ("distinct_fingerprints", "active_hours", "active_days"):
                m[k] = max(m[k], p.get(k) or 0)
            for k in ("is_service_account", "principal_domain", "matches_bi", "matches_powerbi", "matches_etl", "matches_ds"):
                m[k] = m.get(k) or p.get(k)
            m["projects"].add(f"{w['project_id']}/{w['location']}")
    consumers = []
    class_tot = defaultdict(Counter)
    for p in merged.values():
        cls, conf, why = classify_principal(p, labels_by_principal, rules)
        p["projects"] = sorted(p["projects"])
        consumers.append({**p, "workload_class": cls, "class_confidence": conf, "class_reason": why})
        class_tot[cls]["jobs"] += p.get("jobs") or 0
        class_tot[cls]["slot_ms"] += p.get("slot_ms") or 0
        class_tot[cls]["bytes_billed"] += p.get("bytes_billed") or 0
        class_tot[cls]["principals"] += 1
    total_slot = sum(c["slot_ms"] for c in class_tot.values()) or 1
    total_jobs = sum(c["jobs"] for c in class_tot.values()) or 1
    classes = [{"class": c, "principals": class_tot[c]["principals"], "jobs": class_tot[c]["jobs"],
                "jobs_pct": round(100 * class_tot[c]["jobs"] / total_jobs, 1),
                "slot_hours": round(class_tot[c]["slot_ms"] / 3.6e6, 1),
                "slot_pct": round(100 * class_tot[c]["slot_ms"] / total_slot, 1),
                "bytes_billed": class_tot[c]["bytes_billed"]} for c in CLASSES if class_tot.get(c)]

    pbi = [c for c in consumers if c.get("matches_powerbi")]
    pbi_ev = {"principals": len(pbi), "jobs": sum(c.get("jobs") or 0 for c in pbi),
              "slot_hours": round(sum(c.get("slot_ms") or 0 for c in pbi) / 3.6e6, 1),
              "bytes_billed": sum(c.get("bytes_billed") or 0 for c in pbi),
              "note": "Baseado em padrões de principal (powerbi.principal_patterns); confirme as contas do gateway/serviço."}

    tables = Counter()
    tmeta = {}
    for w in wl:
        for t in w.get("tables_read") or []:
            tables[t["table_ref"]] += t.get("jobs") or 0
            tmeta[t["table_ref"]] = t
    top_tables = [{"table_ref": r, "jobs": n, "principals": tmeta[r].get("principals"), "last_read": tmeta[r].get("last_read"),
                   "bi_principals": tmeta[r].get("bi_principals")} for r, n in tables.most_common(30)]

    # Sazonalidade
    dvals = [daily[d]["jobs"] for d in sorted(daily)]
    weekday = [daily[d]["jobs"] for d in daily if datetime.fromisoformat(d).weekday() < 5]
    weekend = [daily[d]["jobs"] for d in daily if datetime.fromisoformat(d).weekday() >= 5]
    month_edge = [daily[d]["jobs"] for d in daily if int(d[8:10]) >= 26 or int(d[8:10]) <= 5]
    month_mid = [daily[d]["jobs"] for d in daily if 6 <= int(d[8:10]) <= 25]
    peak_hour = max(hour_profile, key=lambda h: hour_profile[h]["jobs"]) if hour_profile else None
    season = {
        "daily_cv": round(statistics.pstdev(dvals) / statistics.mean(dvals), 2) if len(dvals) > 1 and statistics.mean(dvals) else None,
        "weekend_vs_weekday": round(statistics.mean(weekend) / statistics.mean(weekday), 2) if weekend and weekday and statistics.mean(weekday) else None,
        "month_edge_vs_mid": round(statistics.mean(month_edge) / statistics.mean(month_mid), 2) if month_edge and month_mid and statistics.mean(month_mid) else None,
        "peak_hour_local": peak_hour,
        "peak_day": max(daily, key=lambda d: daily[d]["jobs"]) if daily else None,
        "timezone": str(tz),
    }
    statements = tot["statements"] or 1
    return {
        "available": True, "window_days": window, "observed_days": len(daily),
        "totals": {"jobs": tot["jobs"], "statements": tot["statements"], "scripts": tot["scripts"], "child_jobs": tot["child_jobs"],
                   "failed": tot["failed"], "failure_rate": round(tot["failed"] / max(tot["jobs"], 1), 4),
                   "cache_hit_rate": round(tot["cache_hits"] / statements, 4),
                   "bytes_processed": tot["bytes_processed"], "bytes_billed": tot["bytes_billed"],
                   "on_demand_bytes_billed": tot["on_demand_bytes_billed"], "slot_ms": tot["slot_ms"],
                   "avg_run_ms": round(tot["sum_run_ms"] / max(tot["jobs"], 1), 1),
                   "avg_queue_ms": round(tot["sum_queue_ms"] / max(tot["jobs"], 1), 1), "max_run_ms": tot["max_run_ms"],
                   "jobs_per_day": round(tot["jobs"] / days, 1)},
        "by_type": [{"job_type": k[0], "priority": k[1], "jobs": v["jobs"], "bytes_billed": v["bytes_billed"],
                     "slot_hours": round(v["slot_ms"] / 3.6e6, 2), "failed": v["failed"],
                     "avg_run_ms": round(v["sum_run_ms"] / max(v["jobs"], 1), 1)} for k, v in sorted(by_type.items(), key=lambda x: -x[1]["jobs"])],
        "daily": [{"day": d, **v} for d, v in sorted(daily.items())],
        "hour_profile": [{"hour": h, "avg_jobs": round(hour_profile[h]["jobs"] / days, 1),
                          "slot_hours_per_day": round(hour_profile[h]["slot_ms"] / 3.6e6 / days, 2)} for h in range(24)],
        "dow_profile": [{"dow": DOW[d], "jobs": dow_profile[d]["jobs"]} for d in range(7)],
        "heatmap": {f"{DOW[d]}-{h:02d}": n for (d, h), n in sorted(heat.items())},
        "percentiles_estimated": percentiles, "percentiles_exact": exact,
        "slots": slots, "seasonality": season,
        "fingerprint_summary": dict(fs),
        "top_by_cost": top("bytes_billed"), "top_by_slots": top("slot_ms"), "top_by_duration": top("avg_run_ms"),
        "top_by_volume": top("bytes_processed"), "top_recurring": top("runs"),
        "consumers": sorted(consumers, key=lambda c: -(c.get("slot_ms") or 0)),
        "classes": classes, "powerbi_evidence": pbi_ev, "top_tables": top_tables,
    }
