"""Volumetria e crescimento (item 5) + Cloud Storage (item 8).

Nada é extrapolado: crescimento só é reportado quando a série do Cloud
Monitoring (ou PARTITIONS) cobre a janela solicitada.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from analyzers.inventory import MIGRATABLE_TYPES

GiB, TiB = 1024 ** 3, 1024 ** 4
SIZE_CLASSES = [(GiB, "< 1 GiB"), (10 * GiB, "1-10 GiB"), (100 * GiB, "10-100 GiB"), (TiB, "100 GiB-1 TiB"),
                (10 * TiB, "1-10 TiB"), (float("inf"), "> 10 TiB")]


def _units(b: float | None) -> dict:
    b = b or 0
    return {"bytes": int(b), "GB": round(b / 1e9, 2), "TB": round(b / 1e12, 3), "GiB": round(b / GiB, 2), "TiB": round(b / TiB, 3)}


def _growth(series: dict[str, float], windows=(30, 60, 90, 180)) -> dict:
    if not series:
        return {w: None for w in windows} | {"coverage_days": 0}
    days = sorted(series)
    last_day = datetime.fromisoformat(days[-1])
    last = series[days[-1]]
    out = {"coverage_days": (last_day - datetime.fromisoformat(days[0])).days, "first_day": days[0], "last_day": days[-1],
           "latest_bytes": last}
    for w in windows:
        target = (last_day - timedelta(days=w)).date().isoformat()
        prior = [d for d in days if d <= target]
        if not prior or (datetime.fromisoformat(target) - datetime.fromisoformat(prior[-1])).days > 3:
            out[w] = None
            continue
        base = series[prior[-1]]
        out[w] = {"from_bytes": base, "delta_bytes": last - base,
                  "pct": round(100 * (last - base) / base, 2) if base else None,
                  "avg_daily_delta_bytes": round((last - base) / w)}
    return out


def analyze(inv: dict, config: dict, workloads: dict | None = None) -> dict:
    tables = [t for t in inv.get("tables_by_ref", {}).values() if not t.get("deleted")]
    low_days = int(config.get("bigquery", {}).get("low_usage_days", 90))
    has_workloads = bool(inv.get("workloads"))
    mig = [t for t in tables if t.get("table_type") in MIGRATABLE_TYPES]
    snap = [t for t in tables if t.get("table_type") in ("SNAPSHOT", "CLONE")]
    tot = Counter()
    for t in mig:
        for k in ("total_logical_bytes", "active_logical_bytes", "long_term_logical_bytes", "total_physical_bytes",
                  "active_physical_bytes", "long_term_physical_bytes", "time_travel_physical_bytes", "fail_safe_physical_bytes",
                  "total_rows"):
            tot[k] += t.get(k) or 0
    snap_phys = sum((t.get("total_physical_bytes") or 0) for t in snap)
    by_project, by_dataset, by_location = defaultdict(Counter), defaultdict(Counter), defaultdict(Counter)
    dist = Counter()
    unpartitioned, low_usage = [], []
    now = datetime.now(timezone.utc)
    for t in mig:
        lb = t.get("total_logical_bytes") or 0
        by_project[t["project_id"]]["logical"] += lb
        by_project[t["project_id"]]["physical"] += t.get("total_physical_bytes") or 0
        by_project[t["project_id"]]["tables"] += 1
        loc = t.get("location") or "desconhecida"
        by_location[loc]["logical"] += lb
        by_location[loc]["physical"] += t.get("total_physical_bytes") or 0
        by_location[loc]["tables"] += 1
        ds = f"{t['project_id']}.{t['dataset']}"
        by_dataset[ds]["logical"] += lb
        by_dataset[ds]["physical"] += t.get("total_physical_bytes") or 0
        by_dataset[ds]["long_term"] += t.get("long_term_logical_bytes") or 0
        by_dataset[ds]["tables"] += 1
        by_dataset[ds]["rows"] += t.get("total_rows") or 0
        for limit, label in SIZE_CLASSES:
            if lb < limit:
                dist[label] += 1
                break
        partitioned = bool(t.get("partition_columns")) or (t.get("total_partitions") or 0) > 1
        t["partitioned"] = partitioned
        t["clustered"] = bool(t.get("clustering_columns"))
        if t.get("table_type") == "BASE TABLE" and not partitioned and lb >= 1 * GiB:
            unpartitioned.append(t)
        last_mod = t.get("storage_last_modified")
        idle = None
        if last_mod:
            try:
                idle = (now - datetime.fromisoformat(last_mod.replace("Z", "+00:00"))).days
            except ValueError:
                pass
        t["days_since_modified"] = idle
        if has_workloads and not t.get("reads_jobs") and not t.get("write_jobs") and (idle is None or idle > low_days):
            low_usage.append(t)
    top20 = sorted(mig, key=lambda t: -(t.get("total_logical_bytes") or 0))[:20]

    # Crescimento (Cloud Monitoring stored_bytes por projeto)
    hist = inv.get("storage_history") or {}
    merged: dict[str, float] = defaultdict(float)
    streaming: dict[str, float] = defaultdict(float)
    for pid, series in hist.items():
        for d, v in (series.get("stored_bytes") or {}).items():
            merged[d] += v
        for d, v in (series.get("uploaded_bytes") or {}).items():
            streaming[d] += v
    growth = _growth(dict(merged))
    part_daily = defaultdict(float)
    for p in inv.get("partitions_daily") or []:
        part_daily[str(p.get("day"))] += p.get("logical_bytes") or 0
    ingestion = {
        "streaming_bytes_per_day": round(sum(streaming.values()) / len(streaming)) if streaming else None,
        "streaming_source": "Cloud Monitoring bigquery storage/uploaded_bytes" if streaming else None,
        "net_growth_bytes_per_day": (growth.get(30) or {}).get("avg_daily_delta_bytes") if isinstance(growth.get(30), dict) else None,
        "modified_partitions_bytes_per_day": round(sum(part_daily.values()) / len(part_daily)) if part_daily else None,
        "modified_partitions_note": "Proxy: bytes lógicos das partições modificadas por dia (superestima se partições forem reescritas)" if part_daily else None,
    }
    written = [t for t in mig if t.get("write_jobs")]
    written_bytes = sum(t.get("total_logical_bytes") or 0 for t in written)

    # GCS
    buckets = inv.get("buckets") or []
    gcs_by_class = Counter()
    gcs_total = 0
    objects = 0
    fmt = Counter()
    small = sampled = 0
    for b in buckets:
        for sc, v in (b.get("bytes_by_class") or {}).items():
            gcs_by_class[sc] += v
            gcs_total += v
        objects += b.get("object_count") or 0
        s = b.get("sample") or {}
        fmt.update(s.get("formats") or {})
        small += s.get("small_files_sampled") or 0
        sampled += s.get("objects_sampled") or 0
    gcs_series = defaultdict(float)
    for pid, m in (inv.get("gcs_monitoring") or {}).items():
        for d, v in (m.get("daily_total_bytes") or {}).items():
            gcs_series[d] += v
    return {
        "tables_counted": len(mig), "snapshots_clones": len(snap),
        "logical": _units(tot["total_logical_bytes"]), "physical": _units(tot["total_physical_bytes"]),
        "active_logical": _units(tot["active_logical_bytes"]), "long_term_logical": _units(tot["long_term_logical_bytes"]),
        "active_physical": _units(tot["active_physical_bytes"]), "long_term_physical": _units(tot["long_term_physical_bytes"]),
        "time_travel_physical": _units(tot["time_travel_physical_bytes"]), "fail_safe_physical": _units(tot["fail_safe_physical_bytes"]),
        "snapshot_clone_physical": _units(snap_phys), "total_rows": tot["total_rows"],
        "compression_ratio_observed": round(tot["total_logical_bytes"] / tot["total_physical_bytes"], 2) if tot["total_physical_bytes"] else None,
        "by_project": [{"project_id": p, "logical_bytes": v["logical"], "physical_bytes": v["physical"], "tables": v["tables"]}
                       for p, v in sorted(by_project.items(), key=lambda x: -x[1]["logical"])],
        "by_location": [{"location": k, "logical_bytes": v["logical"], "physical_bytes": v["physical"], "tables": v["tables"]}
                        for k, v in sorted(by_location.items(), key=lambda x: -x[1]["logical"])],
        "by_dataset": [{"dataset": d, "logical_bytes": v["logical"], "physical_bytes": v["physical"], "long_term_bytes": v["long_term"],
                        "tables": v["tables"], "rows": v["rows"]} for d, v in sorted(by_dataset.items(), key=lambda x: -x[1]["logical"])],
        "size_distribution": {label: dist.get(label, 0) for _, label in SIZE_CLASSES},
        "top20": [{"table": t["ref"], "type": t.get("table_type"), "logical_bytes": t.get("total_logical_bytes"),
                   "physical_bytes": t.get("total_physical_bytes"), "rows": t.get("total_rows"), "partitioned": t.get("partitioned"),
                   "clustered": t.get("clustered"), "reads_jobs": t.get("reads_jobs")} for t in top20],
        "unpartitioned_large": [{"table": t["ref"], "logical_bytes": t.get("total_logical_bytes"), "reads_jobs": t.get("reads_jobs")}
                                for t in sorted(unpartitioned, key=lambda t: -(t.get("total_logical_bytes") or 0))[:50]],
        "unpartitioned_large_count": len(unpartitioned),
        "low_usage": {"available": has_workloads, "count": len(low_usage),
                      "bytes": sum(t.get("total_logical_bytes") or 0 for t in low_usage),
                      "criteria": f"sem leitura/escrita na janela de JOBS e sem modificação há > {low_days} dias",
                      "sample": [t["ref"] for t in sorted(low_usage, key=lambda t: -(t.get("total_logical_bytes") or 0))[:30]]},
        "growth": growth, "ingestion": ingestion,
        "initial_migration": {"logical": _units(tot["total_logical_bytes"]), "physical": _units(tot["total_physical_bytes"]),
                              "excluding_low_usage_logical": _units(tot["total_logical_bytes"] - sum(t.get("total_logical_bytes") or 0 for t in low_usage)),
                              "note": "Volume lógico = não comprimido; o volume em Delta tende a ficar próximo do físico (colunar comprimido) — validar na POC."},
        "incremental": {"tables_with_writes": len(written), "bytes_of_tables_with_writes": written_bytes,
                        "daily_ingestion_evidence": ingestion},
        "gcs": {"buckets": len(buckets), "total": _units(gcs_total), "by_storage_class": {k: _units(v) for k, v in gcs_by_class.items()},
                "objects": objects, "formats_sampled": dict(fmt.most_common(15)),
                "small_file_ratio_sampled": round(small / sampled, 3) if sampled else None, "objects_sampled": sampled,
                "growth": _growth(dict(gcs_series))},
    }
