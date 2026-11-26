"""Insumos de sizing para Databricks (item 15).

NÃO converte slots do BigQuery em DBUs. Usa o workload observado como insumo e
lista os benchmarks que precisam ser executados em uma POC para calibrar o
consumo real em Databricks SQL / Jobs.
"""
from __future__ import annotations


def _get(d, *path, default=None):
    for p in path:
        if not isinstance(d, dict):
            return default
        d = d.get(p)
    return d if d is not None else default


def analyze(inv: dict, *, volume: dict, workloads: dict, pipelines: dict, pbi: dict, security: dict, readiness: dict,
            footprint: dict) -> dict:
    t = workloads.get("totals") or {}
    slots = workloads.get("slots") or {}
    season = workloads.get("seasonality") or {}
    classes = {c["class"]: c for c in workloads.get("classes") or []}
    days = max(workloads.get("observed_days") or 1, 1)
    g30 = (volume.get("growth") or {}).get(30)
    monthly_growth = g30["delta_bytes"] if isinstance(g30, dict) else None
    humans = sum(1 for c in workloads.get("consumers") or [] if not c.get("is_service_account"))
    queries_day = sum(d.get("queries", 0) for d in workloads.get("daily") or []) / days if workloads.get("daily") else None
    peak_conc = slots.get("peak_concurrent_jobs")
    inputs = {
        "initial_migration_volume": volume.get("initial_migration"),
        "active_storage": {"logical": volume.get("active_logical"), "physical": volume.get("active_physical")},
        "long_term_storage": volume.get("long_term_logical"),
        "gcs_volume": _get(volume, "gcs", "total"),
        "daily_ingestion": volume.get("ingestion"),
        "monthly_growth_bytes": monthly_growth,
        "monthly_growth_source": "Cloud Monitoring stored_bytes (30 dias)" if monthly_growth is not None else "sem histórico suficiente",
        "pipelines": {"total": pipelines.get("total"), "by_technology": pipelines.get("by_technology"),
                      "runs_per_day": pipelines.get("runs_per_day_total"), "by_frequency": pipelines.get("by_frequency"),
                      "batch": (pipelines.get("by_processing") or {}).get("batch"), "streaming": pipelines.get("streaming"),
                      "processing_window": pipelines.get("processing_window")},
        "queries": {"jobs_per_day": t.get("jobs_per_day"), "queries_per_day": round(queries_day, 1) if queries_day else None,
                    "peak_hour_local": season.get("peak_hour_local"), "hour_profile": workloads.get("hour_profile")},
        "users": {"bigquery_human_principals": humans, "powerbi_active_viewers": pbi.get("active_viewers"),
                  "powerbi_activity_window_days": pbi.get("activity_window_days")},
        "concurrency": {"peak_concurrent_jobs": peak_conc, "avg_concurrent_jobs_active_minutes": slots.get("avg_concurrent_jobs_active_minutes"),
                        "note": slots.get("note")},
        "peaks": {"peak_minute_slots_bigquery": slots.get("peak_minute_slots"), "p95_hourly_avg_slots": slots.get("p95_hourly_avg_slots"),
                  "peak_day": season.get("peak_day"), "month_edge_factor": season.get("month_edge_vs_mid"),
                  "weekend_factor": season.get("weekend_vs_weekday")},
        "latency_observed": {"p50_ms": _get(workloads, "percentiles_estimated", "p50_ms"),
                             "p95_ms": _get(workloads, "percentiles_estimated", "p95_ms"),
                             "p99_ms": _get(workloads, "percentiles_estimated", "p99_ms"),
                             "required": "não documentado — levantar SLAs com o negócio"},
        "bi": {"reports": pbi.get("reports"), "semantic_models": pbi.get("semantic_models"), "storage_modes": pbi.get("storage_modes"),
               "models_on_bigquery": pbi.get("models_using_bigquery"), "refresh_p50_s": _get(pbi, "refresh", "p50_duration_s")},
        "workloads_by_class": {k: {"slot_hours": v["slot_hours"], "jobs": v["jobs"], "slot_pct": v["slot_pct"]} for k, v in classes.items()},
        "ml_ai": {"vertex_in_use": any(r["key"] == "vertex" and r["status"].startswith("Em uso") for r in footprint.get("services") or []),
                  "bqml_statements": (classes.get("Machine Learning") or {}).get("jobs")},
        "availability": {"production_projects": (footprint.get("environments") or {}).get("prod"),
                         "requirement": "não documentado — levantar RTO/RPO"},
        "network_security": {"vpc_sc_perimeters": _get(security, "summary", "vpc_sc_perimeters"),
                             "private_connectivity": footprint.get("network"),
                             "cmek_datasets": _get(security, "summary", "datasets_cmek"),
                             "sensitive_name_columns": _get(security, "summary", "sensitive_name_columns"),
                             "data_residency": [r for r in (readiness.get("regions") or {}).get("regions") or []]},
        "bigquery_reference_only": {"slot_hours_window": slots.get("slot_hours"), "avg_slots_window": slots.get("avg_slots_window"),
                                    "bytes_billed_window": t.get("bytes_billed"), "window_days": workloads.get("window_days"),
                                    "warning": "Referência de consumo no BigQuery. NÃO converter diretamente em DBUs."},
    }

    def scen(mult_q, mult_conc, growth_months, label):
        lg = _get(volume, "logical", "bytes", default=0)
        stor = lg + (monthly_growth or 0) * growth_months
        conc = (peak_conc or 0) * mult_conc
        return {"scenario": label,
                "storage_logical_bytes_12m": round(stor) if lg else None,
                "queries_per_day": round((queries_day or 0) * mult_q, 1) if queries_day else None,
                "peak_concurrent_queries": round(conc, 1) if peak_conc else None,
                "pipelines_runs_per_day": round((pipelines.get("runs_per_day_total") or 0) * mult_q, 1),
                "assumptions": f"consultas ×{mult_q:.2f}, concorrência ×{mult_conc:.2f}, crescimento de {growth_months} meses ao ritmo observado"}

    scenarios = [scen(0.8, 1.0, 6, "Conservador"), scen(1.0, 1.2, 12, "Esperado"),
                 scen(1.0 * (season.get("month_edge_vs_mid") or 1.0) * 1.3, 1.5 * (season.get("month_edge_vs_mid") or 1.0), 12, "Pico")]
    benchmarks = [
        {"benchmark": "Replay das top consultas por custo/slots", "inputs": [f.get("fp") for f in (workloads.get("top_by_slots") or [])[:20]],
         "goal": "Medir tempo e DBU por consulta em Databricks SQL serverless (Photon) vs BigQuery"},
        {"benchmark": "Concorrência de BI (DirectQuery)", "inputs": sorted({m["model"] for m in pbi.get("correlations") or []})[:5],
         "goal": f"Validar latência sob concorrência (pico observado: {peak_conc or 'N'} jobs ativos no minuto, limite superior) e calibrar min/max clusters na POC; não há conversão confiável de concorrência em clusters"},
        {"benchmark": "Pipelines críticos (janela batch)", "inputs": [p["name"] for p in pipelines.get("longest") or []][:10],
         "goal": "Comparar duração e custo em Lakeflow Jobs serverless/classic"},
        {"benchmark": "Carga inicial / backfill", "inputs": [t["table"] for t in (volume.get("top20") or [])[:5]],
         "goal": "Medir throughput de exportação/ingestão (GCS->Delta) e tamanho final em Delta"},
        {"benchmark": "CDC / streaming", "inputs": [p for p in pipelines.get("external_dependencies") or []][:5],
         "goal": "Latência ponta a ponta e custo de pipelines contínuos"},
    ]
    return {"inputs": inputs, "scenarios": scenarios, "poc_benchmarks": benchmarks,
            "principles": ["Não converter slots em DBUs.", "Calibrar com benchmarks da POC.",
                           "Separar SQL Analytics (warehouses) de Data Engineering (jobs/pipelines)."]}
