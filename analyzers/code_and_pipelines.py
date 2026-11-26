"""Consolida complexidade de objetos SQL (item 12) e de pipelines (item 7).

Anti-patterns:
  - "estático"   : detectado no texto/feature do SQL (potencial)
  - "confirmado" : corroborado por evidência de execução em JOBS (bytes, cache, duração)
"""
from __future__ import annotations

from collections import Counter, defaultdict

from analyzers.sql_complexity import analyze_features, features_from_row

GiB = 1024 ** 3


def _fp_analysis(f: dict) -> dict | None:
    if f.get("sql_analysis"):
        return f["sql_analysis"]
    if "f_chars" in f:
        return analyze_features(features_from_row(f))
    return None


def sql_objects(inv: dict, workloads: dict) -> dict:
    rows = []
    tables = inv.get("tables_by_ref", {})
    for v in inv.get("views") or []:
        a = v.get("analysis")
        if a:
            rows.append({"object_type": "VIEW", "ref": v["ref"], "complexity": a["complexity"], "score": a["score"],
                         "static_anti_patterns": a["anti_patterns"], "confirmed_anti_patterns": [],
                         "refactor_reasons": a.get("refactor_reasons", []), "parser": a.get("parser")})
    for r in inv.get("routines") or []:
        a = r.get("analysis")
        extra = []
        if r.get("language") and r["language"] not in ("SQL", ""):
            label = "JavaScript UDF" if r["language"].upper() in ("JAVASCRIPT", "JS") else f"UDF {r['language']}"
            if not (a and label in a.get("anti_patterns", [])):
                extra.append(label)
        if r.get("remote"):
            extra.append("Remote function")
        if a or extra:
            cls = a["complexity"] if a else ("Alta" if extra else "Baixa")
            if extra and cls in ("Baixa", "Média"):
                cls = "Alta"
            rows.append({"object_type": r.get("type") or "ROUTINE", "ref": r["ref"], "complexity": cls,
                         "score": a["score"] if a else None, "static_anti_patterns": (a or {}).get("anti_patterns", []) + extra,
                         "confirmed_anti_patterns": [], "refactor_reasons": (a or {}).get("refactor_reasons", []) + extra,
                         "parser": (a or {}).get("parser", "metadata")})
    for pid, cfgs in (inv.get("transfers") or {}).items():
        for c in cfgs or []:
            a = c.get("sql_analysis")
            if a:
                rows.append({"object_type": "SCHEDULED_QUERY", "ref": c.get("display_name") or c.get("name"),
                             "complexity": a["complexity"], "score": a["score"], "static_anti_patterns": a["anti_patterns"],
                             "confirmed_anti_patterns": [], "refactor_reasons": [], "parser": "sqlglot/regex"})
    for w in inv.get("workloads") or []:
        for f in w.get("fingerprints") or []:
            a = _fp_analysis(f)
            confirmed = []
            runs = f.get("runs") or 1
            per_run = (f.get("bytes_processed") or 0) / runs
            refs = f.get("refs") or []
            part_refs = [tables[r] for r in refs if r in tables and tables[r].get("partitioned")]
            ref_bytes = sum(t.get("total_logical_bytes") or 0 for t in part_refs)
            if part_refs and ref_bytes > GiB and per_run >= 0.8 * ref_bytes:
                confirmed.append("Full scan em tabela particionada (sem partition pruning efetivo)")
            if runs >= 100 and (f.get("cache_hits") or 0) / runs < 0.1 and f.get("statement_type") == "SELECT":
                confirmed.append("Consulta recorrente sem aproveitamento de cache")
            if a and "SELECT *" in a.get("anti_patterns", []) and per_run > 10 * GiB:
                confirmed.append("SELECT * com alto volume processado por execução")
            if (f.get("avg_run_ms") or 0) > 600_000 and f.get("priority") == "INTERACTIVE":
                confirmed.append("Consulta interativa > 10 min")
            if (f.get("failed") or 0) / runs > 0.2:
                confirmed.append("Taxa de falha > 20%")
            rows.append({"object_type": "QUERY_FINGERPRINT", "ref": f.get("fp"), "project_id": w["project_id"],
                         "complexity": a["complexity"] if a else None, "score": a["score"] if a else None,
                         "static_anti_patterns": a["anti_patterns"] if a else [], "confirmed_anti_patterns": confirmed,
                         "refactor_reasons": a.get("refactor_reasons", []) if a else [], "parser": (a or {}).get("parser"),
                         "runs": runs, "bytes_billed": f.get("bytes_billed"), "slot_ms": f.get("slot_ms"),
                         "avg_run_ms": f.get("avg_run_ms"), "statement_type": f.get("statement_type"), "refs": refs})
    for m in (inv.get("powerbi") or {}).get("semantic_models") or []:
        for cls, n in (m.get("native_query_complexity") or {}).items():
            rows.append({"object_type": "PBI_NATIVE_QUERY", "ref": f"{m['name']} ({n})", "complexity": cls, "score": None,
                         "static_anti_patterns": [], "confirmed_anti_patterns": [], "refactor_reasons": [], "parser": "sqlglot/regex"})
    dist = defaultdict(Counter)
    ap_static, ap_conf = Counter(), Counter()
    for r in rows:
        if r.get("complexity"):
            dist[r["object_type"]][r["complexity"]] += 1
        ap_static.update(r["static_anti_patterns"])
        ap_conf.update(r["confirmed_anti_patterns"])
    return {"objects": rows, "distribution": {k: dict(v) for k, v in dist.items()},
            "static_anti_patterns": dict(ap_static.most_common()), "confirmed_anti_patterns": dict(ap_conf.most_common()),
            "objects_analyzed": len(rows), "sql_mode": inv.get("sql_mode"),
            "routine_languages": dict(Counter(r.get("language") for r in inv.get("routines") or [])),
            "remote_functions": sum(1 for r in inv.get("routines") or [] if r.get("remote"))}


def pipeline_summary(inv: dict) -> dict:
    pipes = inv.get("pipelines") or []
    by_tech = Counter(p["technology"].split(" (")[0] for p in pipes)
    for p in pipes:
        score = 0
        score += {"Cloud Composer": 3, "Dataflow": 3, "Dataproc": 3, "Cloud Data Fusion": 3, "Datastream": 2,
                  "Dataform": 2, "Workflows": 2}.get(p["technology"].split(" (")[0], 1)
        score += min(len(p.get("dependencies") or []), 10) / 3
        score += 2 if p.get("processing") == "streaming" else 0
        score += 1 if (p.get("failure_rate") or 0) > 0.1 else 0
        score += 1 if len(p.get("sources") or []) > 3 else 0
        if p.get("sql_complexity") in ("Alta", "Muito alta"):
            score += 2
        p["migration_complexity"] = "Baixa" if score < 3 else "Média" if score < 5 else "Alta" if score < 7 else "Muito alta"
    unstable = [p for p in pipes if (p.get("failure_rate") or 0) > 0.1 and (p.get("runs_observed") or 0) >= 5]
    longest = sorted([p for p in pipes if p.get("max_duration_s")], key=lambda p: -p["max_duration_s"])[:15]
    external = sorted({s for p in pipes for s in p.get("sources") or [] if s.startswith("external:")})
    chains = []
    for p in pipes:
        if p["technology"] == "Cloud Scheduler" and p.get("targets"):
            chains.append(f"Cloud Scheduler {p['name']} -> {p['targets'][0]}")
    opportunities = []
    sq = [p for p in pipes if "scheduled_query" in p["technology"]]
    if len(sq) >= 10:
        opportunities.append(f"{len(sq)} scheduled queries: candidatas a consolidação em Lakeflow Jobs / pipelines declarativos")
    if unstable:
        opportunities.append(f"{len(unstable)} pipelines com taxa de falha > 10%: priorizar revisão antes da migração")
    if by_tech.get("Cloud Scheduler") and by_tech.get("Workflows"):
        opportunities.append("Orquestração distribuída (Scheduler + Workflows + outros): unificar em Lakeflow Jobs")
    cdc = [p for p in pipes if p.get("load_strategy") == "CDC"]
    if cdc:
        opportunities.append(f"{len(cdc)} streams CDC (Datastream): avaliar Lakeflow Connect ou CDC compatível")
    return {"total": len(pipes), "by_technology": dict(by_tech.most_common()),
            "by_frequency": dict(Counter(p.get("frequency") for p in pipes).most_common()),
            "by_load_strategy": dict(Counter(p.get("load_strategy") for p in pipes).most_common()),
            "by_processing": dict(Counter(p.get("processing") for p in pipes).most_common()),
            "by_complexity": dict(Counter(p.get("migration_complexity") for p in pipes)),
            "streaming": sum(1 for p in pipes if p.get("processing") == "streaming"),
            "unstable": [{"name": p["name"], "technology": p["technology"], "failure_rate": p["failure_rate"],
                          "runs": p.get("runs_observed")} for p in unstable[:20]],
            "longest": [{"name": p["name"], "technology": p["technology"], "max_duration_s": p["max_duration_s"],
                         "avg_duration_s": p.get("avg_duration_s")} for p in longest],
            "external_dependencies": external, "chains": chains[:30], "opportunities": opportunities,
            "runs_per_day_total": round(sum(p.get("runs_per_day") or 0 for p in pipes), 1),
            "processing_window": _window(pipes)}


def _window(pipes: list[dict]) -> dict:
    hours = Counter()
    for p in pipes:
        lr = p.get("last_run") or ""
        if len(lr) >= 13 and lr[10] == "T":
            hours[int(lr[11:13])] += 1
    return {"last_run_hour_utc_distribution": dict(sorted(hours.items())),
            "note": "Aproximação pela hora da última execução observada (UTC)."}
