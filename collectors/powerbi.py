"""Assessment Power BI (item 9) — módulo independente do GCP.

Online (Microsoft Entra ID):
  - Admin Scanner API: workspaces/modified -> getInfo (lotes de 100) -> scanStatus -> scanResult
    (limites documentados: 500 requisições/hora, 16 simultâneas; datasetSchema/datasetExpressions
    exigem as configurações de tenant de metadata scanning)
  - admin/capacities, admin/capacities/refreshables (histórico agregado de refresh em capacidades)
  - groups/{id}/datasets/{id}/refreshes e refreshSchedule (exigem acesso ao workspace)
  - admin/activityevents (usuários ativos e visualizações; 1 dia UTC por chamada)
  - gateways (gateways administrados pelo principal)
Offline: PBIP/TMDL, PBIT, model.bim ou JSON exportado do scanner.

Saída normalizada: workspaces, reports, dashboards, dataflows, semantic_models, datasources.
Usuários/proprietários são pseudonimizados.
"""
from __future__ import annotations

import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collectors.powerbi_parsers import dax_metrics, load_offline, parameters_from_expressions, parse_m
from core.context import Context
from utils.http import ApiError
from utils.logging import get_logger

log = get_logger("powerbi")
MODULE = "powerbi"
PBI = "https://api.powerbi.com/v1.0/myorg"


# --------------------------------------------------------------- normalize
def _expr(e):
    return "\n".join(e) if isinstance(e, list) else e


def normalize_model(ctx: Context, *, model_id: str, name: str, workspace_id: str | None, tables: list, relationships: list,
                    roles: list, expressions: list, target_storage_mode: str | None = None, configured_by: str | None = None,
                    created: str | None = None, datasource_ids: list | None = None, upstream: list | None = None,
                    source: str = "scanner", effective_identity_required: bool | None = None) -> dict:
    params = parameters_from_expressions(expressions)
    norm_tables, modes = [], Counter()
    bq_refs, src_types, natives = set(), set(), []
    heavy_total, steps_total = Counter(), 0
    measures, calc_cols, calc_tables = [], 0, 0
    for t in tables or []:
        parts = t.get("partitions") or []
        mode = t.get("storageMode") or (parts[0].get("mode") if parts else None)
        sources = [p.get("source") or {} for p in parts] or [{"expression": s.get("expression")} for s in t.get("source") or []]
        tm = {"connectors": set(), "bq": set(), "steps": 0, "heavy": Counter(), "native": []}
        is_calc_table = any((s.get("type") or "").lower() == "calculated" for s in sources)
        for s in sources:
            expr = _expr(s.get("expression") or s.get("query"))
            if not expr:
                continue
            if is_calc_table:
                continue
            m = parse_m(expr, params)
            tm["connectors"] |= set(m["connectors"])
            tm["bq"] |= set(m["bigquery_refs"])
            tm["steps"] += m["steps"]
            tm["heavy"].update(m["heavy_steps"])
            tm["native"] += m["native_queries"]
            src_types.update(m["source_types"])
            for nq in m["native_queries"]:
                bq_refs.update(nq["referenced_tables"])
        calc_tables += int(is_calc_table)
        ccols = [c for c in t.get("columns") or [] if (c.get("type") or c.get("columnType") or "").lower() == "calculated"
                 or (c.get("expression") and (c.get("type") or "").lower() != "data")]
        calc_cols += len(ccols)
        for ms in t.get("measures") or []:
            dm = dax_metrics(ms.get("expression"))
            measures.append({"table": t.get("name"), "name": ms.get("name"), **{k: dm[k] for k in ("complexity", "score", "chars",
                                                                                                    "iterators", "calculate", "time_intelligence")}})
        modes[(mode or "desconhecido").lower()] += 1
        bq_refs |= tm["bq"]
        heavy_total.update(tm["heavy"])
        steps_total += tm["steps"]
        natives += tm["native"]
        norm_tables.append({"name": t.get("name"), "storage_mode": mode, "columns": len(t.get("columns") or []),
                            "calculated_columns": len(ccols), "measures": len(t.get("measures") or []),
                            "calculated_table": is_calc_table, "connectors": sorted(tm["connectors"]),
                            "bigquery_refs": sorted(tm["bq"]), "m_steps": tm["steps"], "heavy_steps": dict(tm["heavy"]),
                            "native_queries": len(tm["native"]), "hidden": t.get("isHidden", False)})
    rls_roles = [{"name": r.get("name"), "filters": len(r.get("tablePermissions") or []),
                  "dynamic": any(dax_metrics(tp.get("filterExpression"))["uses_userprincipalname"]
                                 for tp in r.get("tablePermissions") or []),
                  "members": len(r.get("members") or [])} for r in roles or []]
    known_modes = {m for m in modes if m != "desconhecido"}
    if len(known_modes) > 1:
        overall = "Composite"
    elif known_modes:
        overall = {"import": "Import", "directquery": "DirectQuery", "dual": "Dual", "directlake": "Direct Lake"}.get(
            next(iter(known_modes)), next(iter(known_modes)))
    else:
        overall = {"abf": "Import (inferido: targetStorageMode=Abf)", "premiumfiles": "Import/Large (inferido)"}.get(
            (target_storage_mode or "").lower(), "desconhecido")
    rels = [{"from": f"{r.get('fromTable')}.{r.get('fromColumn')}", "to": f"{r.get('toTable')}.{r.get('toColumn')}",
             "bidirectional": (r.get("crossFilteringBehavior") or "").lower() in ("bothdirections", "both"),
             "active": r.get("isActive", True) not in (False, "false")} for r in relationships or []]
    dax_cls = Counter(m["complexity"] for m in measures)
    return {
        "id": model_id, "name": name, "workspace_id": workspace_id, "source": source,
        "storage_mode": overall, "table_modes": dict(modes), "target_storage_mode": target_storage_mode,
        "configured_by": ctx.redactor.principal(configured_by) if configured_by else None, "created": created,
        "tables": norm_tables, "table_count": len(norm_tables), "column_count": sum(t["columns"] for t in norm_tables),
        "measure_count": len(measures), "calculated_columns": calc_cols, "calculated_tables": calc_tables,
        "relationship_count": len(rels), "bidirectional_relationships": sum(r["bidirectional"] for r in rels),
        "relationships": rels if source != "scanner" else [], "rls_roles": rls_roles,
        "rls": bool(rls_roles) or bool(effective_identity_required),
        "measures": sorted(measures, key=lambda m: -m["score"])[:50], "dax_complexity": dict(dax_cls),
        "source_types": sorted(src_types), "bigquery_refs": sorted(bq_refs), "native_query_count": len(natives),
        "native_query_complexity": dict(Counter(n["complexity"] for n in natives)),
        "m_steps": steps_total, "heavy_m_steps": dict(heavy_total), "parameters": sorted(params),
        "datasource_ids": datasource_ids or [], "upstream_datasets": upstream or [],
        "uses_bigquery": "bigquery" in src_types or bool(bq_refs),
    }


def _from_scan(ctx: Context, scan: dict, source: str = "scanner") -> dict:
    out = {"workspaces": [], "reports": [], "dashboards": [], "dataflows": [], "semantic_models": [], "datasources": []}
    for ds in scan.get("datasourceInstances") or []:
        cd = ds.get("connectionDetails") or {}
        out["datasources"].append({"id": ds.get("datasourceId"), "type": ds.get("datasourceType"),
                                   "server": ctx.redactor.host(cd.get("server") or cd.get("url") or cd.get("path")),
                                   "database": cd.get("database"), "kind": cd.get("kind"), "gateway_id": ds.get("gatewayId")})
    for ws in scan.get("workspaces") or []:
        wid = ws.get("id")
        out["workspaces"].append({"id": wid, "name": ws.get("name"), "type": ws.get("type"), "state": ws.get("state"),
                                  "capacity_id": ws.get("capacityId"), "dedicated_capacity": ws.get("isOnDedicatedCapacity"),
                                  "users": len(ws.get("users") or [])})
        for r in ws.get("reports") or []:
            out["reports"].append({"id": r.get("id"), "name": r.get("name"), "workspace_id": wid, "dataset_id": r.get("datasetId"),
                                   "dataset_workspace_id": r.get("datasetWorkspaceId") or wid, "type": r.get("reportType"),
                                   "format": r.get("format"), "created": r.get("createdDateTime"),
                                   "modified": r.get("modifiedDateTime"), "app": bool(r.get("appId")),
                                   "sensitivity": (r.get("sensitivityLabel") or {}).get("labelId") is not None})
        for d in ws.get("dashboards") or []:
            out["dashboards"].append({"id": d.get("id"), "name": d.get("displayName"), "workspace_id": wid,
                                      "tiles": len(d.get("tiles") or []),
                                      "tile_datasets": sorted({t.get("datasetId") for t in d.get("tiles") or [] if t.get("datasetId")})})
        for f in ws.get("dataflows") or []:
            out["dataflows"].append({"id": f.get("objectId"), "name": f.get("name"), "workspace_id": wid,
                                     "datasource_ids": [u.get("datasourceInstanceId") for u in f.get("datasourceUsages") or []]})
        for d in ws.get("datasets") or []:
            out["semantic_models"].append(normalize_model(
                ctx, model_id=d.get("id"), name=d.get("name"), workspace_id=wid, tables=d.get("tables") or [],
                relationships=d.get("relationships") or [], roles=d.get("roles") or [], expressions=d.get("expressions") or [],
                target_storage_mode=d.get("targetStorageMode"), configured_by=d.get("configuredBy"), created=d.get("createdDate"),
                datasource_ids=[u.get("datasourceInstanceId") for u in d.get("datasourceUsages") or []],
                upstream=[u.get("targetDatasetId") for u in d.get("upstreamDatasets") or []], source=source,
                effective_identity_required=d.get("isEffectiveIdentityRequired")))
            if d.get("schemaRetrievalError"):
                out["semantic_models"][-1]["schema_error"] = str(d["schemaRetrievalError"])[:200]
    # tipos de datasource por modelo
    ds_types = {d["id"]: d["type"] for d in out["datasources"]}
    for m in out["semantic_models"]:
        types = {ds_types.get(i) for i in m["datasource_ids"] if ds_types.get(i)}
        m["datasource_types"] = sorted(types)
        if any((t or "").lower().startswith("googlebigquery") for t in types):
            m["uses_bigquery"] = True
    return out


# ------------------------------------------------------------------ online
def _scan(ctx: Context) -> dict | None:
    api = ctx.powerbi
    sc = ctx.config["powerbi"].get("scanner", {})
    try:
        mods = api.get(f"{PBI}/admin/workspaces/modified",
                       params={"excludePersonalWorkspaces": "True", "excludeInActiveWorkspaces": "True"})
        ids = [w["id"] for w in (mods if isinstance(mods, list) else mods.get("value", mods.get("_raw", [])) or [])]
        ctx.coverage.ok(MODULE, "workspaces", len(ids))
    except ApiError as exc:
        ctx.coverage.fail(MODULE, "workspaces", exc, scope="admin/workspaces/modified",
                          permission_hint="Tenant setting 'Service principals can use read-only admin APIs'")
        return None
    params = {"lineage": str(sc.get("lineage", True)), "datasourceDetails": str(sc.get("datasource_details", True)),
              "datasetSchema": str(sc.get("dataset_schema", True)), "datasetExpressions": str(sc.get("dataset_expressions", True)),
              "getArtifactUsers": str(sc.get("artifact_users", False))}
    merged = {"workspaces": [], "datasourceInstances": []}
    for i in range(0, len(ids), 100):
        batch = ids[i:i + 100]
        try:
            req = api.post(f"{PBI}/admin/workspaces/getInfo", json_body={"workspaces": batch}, params=params)
            scan_id = req.get("id")
            status, waited = req.get("status"), 0.0
            while status not in ("Succeeded", "Failed") and waited < 600:
                time.sleep(min(5 + waited / 10, 30))
                waited += min(5 + waited / 10, 30)
                status = api.get(f"{PBI}/admin/workspaces/scanStatus/{scan_id}").get("status")
            if status != "Succeeded":
                raise ApiError(0, f"scan {status}", f"{PBI}/admin/workspaces/scanStatus", "scan_failed")
            res = api.get(f"{PBI}/admin/workspaces/scanResult/{scan_id}")
            merged["workspaces"] += res.get("workspaces") or []
            merged["datasourceInstances"] += res.get("datasourceInstances") or []
            ctx.coverage.ok(MODULE, "scanner", len(res.get("workspaces") or []))
        except ApiError as exc:
            ctx.coverage.fail(MODULE, "scanner", exc, scope=f"lote {i // 100 + 1}")
    if sc.get("dataset_schema") and not any(d.get("tables") for w in merged["workspaces"] for d in w.get("datasets") or []):
        ctx.coverage.limitation(MODULE, "scanner", "Scanner não retornou tabelas/expressões: verificar tenant settings "
                                                   "'Enhance admin APIs responses with detailed metadata' e '... with DAX and mashup expressions'")
    return merged


def _capacities_and_refresh(ctx: Context, models: list[dict]) -> dict:
    api = ctx.powerbi
    out = {"capacities": [], "refreshables": [], "refresh_history": [], "refresh_schedules": [], "gateways": []}
    try:
        out["capacities"] = [{"id": c.get("id"), "name": c.get("displayName"), "sku": c.get("sku"), "state": c.get("state"),
                              "region": c.get("region")} for c in api.paginate_odata(f"{PBI}/admin/capacities")]
        ctx.coverage.ok(MODULE, "capacities", len(out["capacities"]))
    except ApiError as exc:
        ctx.coverage.fail(MODULE, "capacities", exc, scope="admin/capacities")
    try:
        for r in api.paginate_odata(f"{PBI}/admin/capacities/refreshables", {"$top": 1000, "$expand": "capacity,group"}):
            last = r.get("lastRefresh") or {}
            out["refreshables"].append({
                "dataset_id": r.get("id"), "name": r.get("name"), "workspace_id": (r.get("group") or {}).get("id"),
                "capacity_sku": (r.get("capacity") or {}).get("sku"), "refresh_count": r.get("refreshCount"),
                "refresh_failures": r.get("refreshFailures"), "avg_duration_s": r.get("averageDuration"),
                "median_duration_s": r.get("medianDuration"), "refreshes_per_day": r.get("refreshesPerDay"),
                "last_status": last.get("status"), "last_start": last.get("startTime"),
                "schedule_enabled": (r.get("refreshSchedule") or {}).get("enabled"),
                "schedule_times": len((r.get("refreshSchedule") or {}).get("times") or []),
                "schedule_days": len((r.get("refreshSchedule") or {}).get("days") or [])})
        ctx.coverage.ok(MODULE, "refresh", len(out["refreshables"]))
    except ApiError as exc:
        ctx.coverage.fail(MODULE, "refresh", exc, scope="admin/capacities/refreshables")
    top = int(ctx.config["powerbi"].get("refresh_history_top", 30))
    cap = int(ctx.config["powerbi"].get("max_refresh_datasets", 300))
    covered = {r["dataset_id"] for r in out["refreshables"]}
    candidates = [m for m in models if m.get("workspace_id") and m["id"] not in covered][:cap]
    denied = 0
    for m in candidates:
        base = f"{PBI}/groups/{m['workspace_id']}/datasets/{m['id']}"
        try:
            hist = api.get(f"{base}/refreshes", params={"$top": top}).get("value") or []
            for h in hist:
                start, end = h.get("startTime"), h.get("endTime")
                dur = None
                if start and end:
                    try:
                        dur = (datetime.fromisoformat(end.replace("Z", "+00:00")) -
                               datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds()
                    except ValueError:
                        pass
                out["refresh_history"].append({"dataset_id": m["id"], "workspace_id": m["workspace_id"],
                                               "type": h.get("refreshType"), "status": h.get("status"), "start": start,
                                               "duration_s": dur})
            sched = api.get(f"{base}/refreshSchedule")
            out["refresh_schedules"].append({"dataset_id": m["id"], "enabled": sched.get("enabled"),
                                             "days": len(sched.get("days") or []), "times": len(sched.get("times") or []),
                                             "timezone": sched.get("localTimeZoneId")})
            ctx.coverage.ok(MODULE, "refresh", 1)
        except ApiError as exc:
            denied += 1
            if denied <= 3:
                ctx.coverage.fail(MODULE, "refresh", exc, scope=f"dataset {m['id'][:8]}",
                                  permission_hint="Service principal precisa ser membro (Viewer+) do workspace para refresh history")
            else:
                ctx.coverage.entry(MODULE, "refresh").scope_errors += 1
    try:
        out["gateways"] = [{"id": g.get("id"), "name": g.get("name"), "type": g.get("type")}
                           for g in api.paginate_odata(f"{PBI}/gateways")]
        ctx.coverage.ok(MODULE, "gateways", len(out["gateways"]))
    except ApiError as exc:
        ctx.coverage.fail(MODULE, "gateways", exc, scope="gateways")
    return out


def _activity(ctx: Context) -> dict:
    days = min(int(ctx.config["powerbi"].get("activity_days", 28)), 28)
    api = ctx.powerbi
    users, views = set(), Counter()
    daily_users: dict[str, int] = {}
    hourly = Counter()
    today = datetime.now(timezone.utc).date()
    for i in range(1, days + 1):
        d = today - timedelta(days=i)
        start, end = f"'{d.isoformat()}T00:00:00.000Z'", f"'{d.isoformat()}T23:59:59.999Z'"
        day_users = set()
        try:
            for ev in api.paginate_odata(f"{PBI}/admin/activityevents",
                                         {"startDateTime": start, "endDateTime": end, "$filter": "Activity eq 'viewreport'"}):
                u = ctx.redactor.pseudonym(ev.get("UserId") or ev.get("UserKey"), "pbiuser")
                users.add(u)
                day_users.add(u)
                views[ev.get("ReportId")] += 1
                ts = ev.get("CreationTime") or ""
                if len(ts) >= 13:
                    hourly[ts[11:13]] += 1
            daily_users[d.isoformat()] = len(day_users)
            ctx.coverage.ok(MODULE, "activity", 1)
        except ApiError as exc:
            ctx.coverage.fail(MODULE, "activity", exc, scope=d.isoformat())
            if exc.kind in ("permission_denied", "unauthenticated"):
                break
    return {"window_days": days, "distinct_viewers": len(users), "daily_viewers": daily_users,
            "views_by_report": dict(views), "views_by_hour_utc": dict(sorted(hourly.items()))}


# -------------------------------------------------------------------- main
def collect(ctx: Context) -> dict:
    pcfg = ctx.config["powerbi"]
    result = {"mode": None, "workspaces": [], "reports": [], "dashboards": [], "dataflows": [], "semantic_models": [],
              "datasources": [], "refresh": {}, "activity": {}, "offline_warnings": []}
    offline = pcfg.get("offline_path")
    if offline:
        result["mode"] = "offline"
        artifacts, warnings = load_offline(offline)
        result["offline_warnings"] = warnings
        for a in artifacts:
            if a["kind"] == "scanner":
                part = _from_scan(ctx, a["scan"], source="scanner-offline")
                for k in ("workspaces", "reports", "dashboards", "dataflows", "semantic_models", "datasources"):
                    result[k] += part[k]
                continue
            m = a["model"]
            model = normalize_model(ctx, model_id=f"offline:{a['name']}", name=a["name"], workspace_id=None,
                                    tables=m["tables"], relationships=m["relationships"], roles=m["roles"],
                                    expressions=m["expressions"], source=a["kind"])
            result["semantic_models"].append(model)
            if a.get("report"):
                result["reports"].append({"id": f"offline:{a['name']}", "name": a["name"], "workspace_id": None,
                                          "dataset_id": model["id"], "type": "PowerBIReport", **a["report"]})
        ctx.coverage.ok(MODULE, "offline_artifacts", len(artifacts))
        for w in warnings:
            ctx.coverage.limitation(MODULE, "offline_artifacts", w)
        ctx.coverage.finalize(MODULE, "offline_artifacts", source=f"Artefatos offline ({Path(offline).name})")
    if ctx.powerbi is not None:
        result["mode"] = "online" if not result["mode"] else "online+offline"
        scan = _scan(ctx)
        if scan:
            part = _from_scan(ctx, scan)
            for k in ("workspaces", "reports", "dashboards", "dataflows", "semantic_models", "datasources"):
                result[k] += part[k]
        result["refresh"] = _capacities_and_refresh(ctx, result["semantic_models"])
        result["activity"] = _activity(ctx)
        for comp, src in [("workspaces", "Admin API workspaces/modified"), ("scanner", "Admin Scanner API"),
                          ("capacities", "Admin API capacities"), ("refresh", "refreshables + datasets/refreshes"),
                          ("gateways", "Gateways API"), ("activity", "Admin API activityevents")]:
            ctx.coverage.finalize(MODULE, comp, source=src)
    if not result["mode"]:
        ctx.coverage.entry(MODULE, "scanner").status = "Not Assessed"
        ctx.coverage.limitation(MODULE, "scanner", "Sem credenciais Entra ID nem powerbi.offline_path: Power BI não avaliado")
        result["mode"] = "not_assessed"
    _link_reports(result)
    ctx.save_raw(MODULE, "powerbi", result)
    return {"models": len(result["semantic_models"]), "reports": len(result["reports"]), "mode": result["mode"]}


def _link_reports(result: dict) -> None:
    by_model = defaultdict(list)
    for r in result["reports"]:
        by_model[r.get("dataset_id")].append(r["id"])
    for m in result["semantic_models"]:
        m["report_ids"] = by_model.get(m["id"], [])
        m["report_count"] = len(m["report_ids"])
