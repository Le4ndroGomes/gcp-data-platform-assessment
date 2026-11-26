"""BigQuery — capacidade: Editions, reservations, assignments, capacity commitments e BI Engine.

Fonte: BigQuery Reservation API v1. Projetos administradores de reservas podem
ser diferentes dos projetos de consulta; além dos projetos-alvo, são avaliados
os projetos derivados de `reservation_id` observado em JOBS (formato
`admin-project:LOCATION.reservation`) e os listados em
`bigquery.reservation_admin_projects`.
"""
from __future__ import annotations

from core.context import Context
from utils.http import ApiError
from utils.logging import get_logger

log = get_logger("bq_capacity")
MODULE = "bigquery_capacity"
RES = "https://bigqueryreservation.googleapis.com/v1"


def _admin_projects_from_jobs(ctx: Context) -> set[tuple[str, str]]:
    found = set()
    wl_dir = ctx.raw_dir / "bigquery_workloads"
    if not wl_dir.exists():
        return found
    for f in wl_dir.glob("workloads__*.json"):
        data = ctx.load_raw("bigquery_workloads", f.stem) or {}
        for r in data.get("by_reservation") or []:
            rid = r.get("reservation_id") or ""
            if ":" in rid and "." in rid:
                proj, rest = rid.split(":", 1)
                found.add((proj, rest.split(".", 1)[0].lower()))
    return found


def collect(ctx: Context) -> dict:
    targets: set[tuple[str, str]] = {(p, loc.lower()) for p in ctx.project_ids() for loc in ctx.regions_for(p)}
    targets |= _admin_projects_from_jobs(ctx)
    for p in ctx.config["bigquery"].get("reservation_admin_projects") or []:
        for loc in {loc for _, loc in targets} or {"us"}:
            targets.add((p, loc))
    out: list[dict] = []

    def per(target):
        pid, loc = target
        base = f"{RES}/projects/{pid}/locations/{loc}"
        rec = {"project_id": pid, "location": loc, "reservations": [], "commitments": [], "assignments": [],
               "bi_engine": None}
        try:
            rec["reservations"] = [{
                "name": r.get("name", "").split("/")[-1], "slot_capacity": int(r.get("slotCapacity") or 0),
                "edition": r.get("edition"), "ignore_idle_slots": r.get("ignoreIdleSlots"),
                "autoscale_max_slots": int((r.get("autoscale") or {}).get("maxSlots") or 0),
                "autoscale_current_slots": int((r.get("autoscale") or {}).get("currentSlots") or 0),
                "concurrency": r.get("concurrency"), "creation_time": r.get("creationTime"),
            } for r in ctx.gcp.paginate(f"{base}/reservations", "reservations", {"pageSize": 200})]
            rec["commitments"] = [{
                "name": c.get("name", "").split("/")[-1], "slot_count": int(c.get("slotCount") or 0),
                "plan": c.get("plan"), "renewal_plan": c.get("renewalPlan"), "state": c.get("state"),
                "edition": c.get("edition"), "start": c.get("commitmentStartTime"), "end": c.get("commitmentEndTime"),
            } for c in ctx.gcp.paginate(f"{base}/capacityCommitments", "capacityCommitments", {"pageSize": 200})]
            rec["assignments"] = [{
                "reservation": a.get("name", "").split("/reservations/")[-1].split("/")[0],
                "assignee": a.get("assignee"), "job_type": a.get("jobType"), "state": a.get("state"),
            } for a in ctx.gcp.paginate(f"{base}/reservations/-/assignments", "assignments", {"pageSize": 200})]
            ctx.coverage.ok(MODULE, "reservations", len(rec["reservations"]) + len(rec["commitments"]))
        except ApiError as exc:
            ctx.coverage.fail(MODULE, "reservations", exc, scope=f"{pid}/{loc}")
        try:
            bi = ctx.gcp.get(f"{base}/biReservation")
            rec["bi_engine"] = {"size_bytes": int(bi.get("size") or 0),
                                "preferred_tables": len(bi.get("preferredTables") or [])}
            ctx.coverage.ok(MODULE, "bi_engine", 1 if rec["bi_engine"]["size_bytes"] else 0)
        except ApiError as exc:
            ctx.coverage.fail(MODULE, "bi_engine", exc, scope=f"{pid}/{loc}")
        out.append(rec)

    ctx.run_parallel(per, sorted(targets), "bigquery_capacity")
    ctx.save_raw(MODULE, "capacity", out)
    ctx.coverage.finalize(MODULE, "reservations", source="BigQuery Reservation API (reservations, capacityCommitments, assignments)")
    ctx.coverage.finalize(MODULE, "bi_engine", source="BigQuery Reservation API biReservation")
    return {"targets": len(targets)}
