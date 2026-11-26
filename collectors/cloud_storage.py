"""Google Cloud Storage (item 8).

- Inventário de buckets via JSON API (metadados de configuração).
- Volume e quantidade de objetos via Cloud Monitoring (métricas agregadas
  `storage/total_bytes` e `storage/object_count`, amostradas 1x/dia) — evita listar
  milhões de objetos.
- Amostragem limitada de nomes de objetos (`storage.sample_objects_per_bucket`)
  para formatos, distribuição de tamanhos, small files e layout (Hive/Delta/Iceberg).
  Nomes de objetos NÃO são gravados; apenas estatísticas derivadas. Conteúdo nunca é lido.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from core.context import Context
from utils.http import ApiError
from utils.logging import get_logger

log = get_logger("gcs")
MODULE = "cloud_storage"
GCS = "https://storage.googleapis.com/storage/v1"
MON = "https://monitoring.googleapis.com/v3"
SIZE_BUCKETS = [(1 << 20, "<1MiB"), (16 << 20, "1-16MiB"), (128 << 20, "16-128MiB"), (1 << 30, "128MiB-1GiB"),
                (float("inf"), ">1GiB")]
KNOWN_EXT = {"parquet", "avro", "csv", "json", "jsonl", "ndjson", "orc", "txt", "gz", "zip", "xml", "xlsx", "xls",
             "pdf", "dcm", "png", "jpg", "jpeg", "tsv", "snappy", "zst", "bz2", "dat", "log", "sql", "py", "jar"}
HIVE_RE = re.compile(r"(?:^|/)([A-Za-z0-9_]+)=[^/]+/")


def _ts(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _monitoring(ctx: Context, pid: str) -> dict:
    end = datetime.now(timezone.utc)
    days = min(max(ctx.days, 30), 180)
    out: dict = {"latest_by_bucket": {}, "daily_total_bytes": {}, "object_count_by_bucket": {}}
    base = {"interval.endTime": _ts(end), "aggregation.alignmentPeriod": "86400s", "view": "FULL"}
    try:
        latest = {**base, "interval.startTime": _ts(end - timedelta(days=3)),
                  "filter": 'metric.type="storage.googleapis.com/storage/total_bytes" AND resource.type="gcs_bucket"',
                  "aggregation.perSeriesAligner": "ALIGN_MEAN", "aggregation.crossSeriesReducer": "REDUCE_SUM",
                  "aggregation.groupByFields": ["resource.label.bucket_name", "metric.label.storage_class"]}
        for ts in ctx.gcp.paginate(f"{MON}/projects/{pid}/timeSeries", "timeSeries", latest):
            b = ts.get("resource", {}).get("labels", {}).get("bucket_name")
            sc = ts.get("metric", {}).get("labels", {}).get("storage_class", "UNKNOWN")
            pts = ts.get("points") or []
            if b and pts:
                v = pts[0].get("value", {})
                out["latest_by_bucket"].setdefault(b, {})[sc] = float(v.get("doubleValue") or v.get("int64Value") or 0)
        cnt = {**latest, "filter": 'metric.type="storage.googleapis.com/storage/object_count" AND resource.type="gcs_bucket"',
               "aggregation.groupByFields": ["resource.label.bucket_name"]}
        for ts in ctx.gcp.paginate(f"{MON}/projects/{pid}/timeSeries", "timeSeries", cnt):
            b = ts.get("resource", {}).get("labels", {}).get("bucket_name")
            pts = ts.get("points") or []
            if b and pts:
                v = pts[0].get("value", {})
                out["object_count_by_bucket"][b] = int(float(v.get("int64Value") or v.get("doubleValue") or 0))
        hist = {**base, "interval.startTime": _ts(end - timedelta(days=days)),
                "filter": 'metric.type="storage.googleapis.com/storage/total_bytes" AND resource.type="gcs_bucket"',
                "aggregation.perSeriesAligner": "ALIGN_MEAN", "aggregation.crossSeriesReducer": "REDUCE_SUM"}
        for ts in ctx.gcp.paginate(f"{MON}/projects/{pid}/timeSeries", "timeSeries", hist):
            for pt in ts.get("points") or []:
                day = (pt.get("interval", {}).get("endTime") or "")[:10]
                v = pt.get("value", {})
                out["daily_total_bytes"][day] = out["daily_total_bytes"].get(day, 0) + float(v.get("doubleValue") or v.get("int64Value") or 0)
        out["daily_total_bytes"] = dict(sorted(out["daily_total_bytes"].items()))
        ctx.coverage.ok(MODULE, "volume_metrics", len(out["latest_by_bucket"]))
    except ApiError as exc:
        ctx.coverage.fail(MODULE, "volume_metrics", exc, scope=pid)
    return out


def _sample(ctx: Context, bucket: str, limit: int, small: int) -> dict:
    ext, sizes, hive_keys = Counter(), Counter(), Counter()
    total = n = smalls = 0
    markers = {"delta_log": 0, "iceberg_metadata": 0, "success_files": 0}
    prefixes = Counter()
    for o in ctx.gcp.paginate(f"{GCS}/b/{bucket}/o", "items",
                              {"maxResults": 1000, "fields": "items(name,size),nextPageToken"}, max_items=limit):
        name = o.get("name", "")
        size = int(o.get("size") or 0)
        n += 1
        total += size
        if name.endswith("/"):
            continue
        base = name.rsplit("/", 1)[-1].lower()
        parts = base.split(".")
        e = parts[-1] if len(parts) > 1 else "(sem extensão)"
        if e in ("gz", "snappy", "zst", "bz2") and len(parts) > 2 and parts[-2] in KNOWN_EXT:
            e = f"{parts[-2]}.{e}"
        ext[e if (e.split(".")[0] in KNOWN_EXT or e == "(sem extensão)") else "outros"] += 1
        for limit_b, label in SIZE_BUCKETS:
            if size < limit_b:
                sizes[label] += 1
                break
        if size < small:
            smalls += 1
        for k in HIVE_RE.findall(name):
            hive_keys[k] += 1
        if "/_delta_log/" in f"/{name}":
            markers["delta_log"] += 1
        if "/metadata/" in name and name.endswith(".metadata.json"):
            markers["iceberg_metadata"] += 1
        if base == "_success":
            markers["success_files"] += 1
        prefixes[name.split("/", 1)[0] if "/" in name else "(raiz)"] += 1
    top_prefixes = [{"prefix": ctx.redactor.pseudonym(p, "prefix") if ctx.redactor.anonymize else p, "objects": c}
                    for p, c in prefixes.most_common(15)]
    return {"objects_sampled": n, "bytes_sampled": total, "formats": dict(ext), "size_distribution": dict(sizes),
            "small_files_sampled": smalls, "small_file_ratio": round(smalls / n, 3) if n else None,
            "avg_object_bytes": round(total / n) if n else None, "hive_partition_keys": dict(hive_keys.most_common(10)),
            "table_format_markers": markers, "top_prefixes": top_prefixes, "sample_truncated": n >= limit}


def collect(ctx: Context) -> dict:
    scfg = ctx.config["storage"]
    buckets: list[dict] = []
    monitoring: dict = {}

    def per_project(pid):
        try:
            items = list(ctx.gcp.paginate(f"{GCS}/b", "items", {"project": pid, "maxResults": 1000,
                                                               "projection": "noAcl"}))
            ctx.coverage.ok(MODULE, "buckets", len(items))
        except ApiError as exc:
            ctx.coverage.fail(MODULE, "buckets", exc, scope=pid)
            return
        monitoring[pid] = _monitoring(ctx, pid)
        def build(b):
            iam = b.get("iamConfiguration") or {}
            rec = {
                "project_id": pid, "bucket": b.get("name"), "location": b.get("location"),
                "location_type": b.get("locationType"), "storage_class": b.get("storageClass"),
                "versioning": (b.get("versioning") or {}).get("enabled", False),
                "lifecycle_rules": len((b.get("lifecycle") or {}).get("rule") or []),
                "lifecycle_actions": sorted({(r.get("action") or {}).get("type", "") for r in (b.get("lifecycle") or {}).get("rule") or []}),
                "retention_seconds": int((b.get("retentionPolicy") or {}).get("retentionPeriod") or 0),
                "retention_locked": (b.get("retentionPolicy") or {}).get("isLocked", False),
                "soft_delete_seconds": int((b.get("softDeletePolicy") or {}).get("retentionDurationSeconds") or 0),
                "cmek": bool((b.get("encryption") or {}).get("defaultKmsKeyName")),
                "uniform_bucket_level_access": (iam.get("uniformBucketLevelAccess") or {}).get("enabled", False),
                "public_access_prevention": iam.get("publicAccessPrevention", "inherited"),
                "autoclass": (b.get("autoclass") or {}).get("enabled", False),
                "hierarchical_namespace": (b.get("hierarchicalNamespace") or {}).get("enabled", False),
                "labels": b.get("labels") or {}, "created": b.get("timeCreated"), "updated": b.get("updated"),
            }
            m = monitoring[pid]
            by_class = m["latest_by_bucket"].get(rec["bucket"])
            rec["bytes_by_class"] = by_class
            rec["total_bytes"] = sum(by_class.values()) if by_class else None
            rec["object_count"] = m["object_count_by_bucket"].get(rec["bucket"])
            try:
                pol = ctx.gcp.get(f"{GCS}/b/{rec['bucket']}/iam")
                members = {mbr for bd in pol.get("bindings") or [] for mbr in bd.get("members") or []}
                rec["public_members"] = sorted(members & {"allUsers", "allAuthenticatedUsers"})
                ctx.coverage.ok(MODULE, "bucket_iam", 1)
            except ApiError as exc:
                rec["public_members"] = None
                ctx.coverage.fail(MODULE, "bucket_iam", exc, scope=rec["bucket"])
            return rec

        buckets.extend(r for r in ctx.run_parallel(build, items, f"buckets/{pid}", workers=ctx.io_workers) if r)

    ctx.run_parallel(per_project, ctx.project_ids(), "cloud_storage")
    buckets.sort(key=lambda r: (r["project_id"], r["bucket"]))

    limit = int(scfg.get("sample_objects_per_bucket", 2000))
    max_b = int(scfg.get("max_buckets_sampled", 200))
    ranked = sorted(buckets, key=lambda b: b.get("total_bytes") or 0, reverse=True)[:max_b]
    if len(buckets) > max_b:
        ctx.coverage.limitation(MODULE, "object_sampling", f"Amostragem em {max_b} de {len(buckets)} buckets (maiores)")

    def sample(rec):
        if limit <= 0:
            return
        try:
            rec["sample"] = _sample(ctx, rec["bucket"], limit, int(scfg.get("small_file_threshold_bytes", 16 << 20)))
            ctx.coverage.ok(MODULE, "object_sampling", 1)
        except ApiError as exc:
            ctx.coverage.fail(MODULE, "object_sampling", exc, scope=rec["bucket"])

    ctx.run_parallel(sample, ranked, "gcs_sampling", workers=ctx.io_workers)
    ctx.save_raw(MODULE, "buckets", buckets)
    ctx.save_raw(MODULE, "monitoring", {p: {"daily_total_bytes": m.get("daily_total_bytes")} for p, m in monitoring.items()})
    ctx.coverage.finalize(MODULE, "buckets", source="Cloud Storage JSON API buckets.list")
    ctx.coverage.finalize(MODULE, "bucket_iam", source="Cloud Storage buckets.getIamPolicy")
    ctx.coverage.finalize(MODULE, "volume_metrics", source="Cloud Monitoring storage/total_bytes, object_count",
                          history_window=f"{min(max(ctx.days, 30), 180)} dias")
    ctx.coverage.finalize(MODULE, "object_sampling", source=f"objects.list (amostra até {limit} por bucket)",
                          confidence="medium")
    return {"buckets": len(buckets)}
