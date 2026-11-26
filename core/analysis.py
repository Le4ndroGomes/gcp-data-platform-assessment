"""Executa todos os analyzers sobre os dados brutos (online ou --analyze-only)."""
from __future__ import annotations

from pathlib import Path

from analyzers import (code_and_pipelines, cost_analysis, inventory, migration_readiness, platform_footprint,
                       security_findings, sizing, use_case, volume_analysis, workload_analysis)
from collectors import lineage
from core.context import Context, load_yaml
from utils.logging import get_logger

log = get_logger("analysis")
BASE_DIR = Path(__file__).resolve().parent.parent


def run(ctx: Context, focus_file: str | None = None) -> dict:
    inv = inventory.build_inventory(ctx)
    wl = workload_analysis.analyze(inv, ctx.config)
    vol = volume_analysis.analyze(inv, ctx.config, wl)
    sql = code_and_pipelines.sql_objects(inv, wl)
    pipes = code_and_pipelines.pipeline_summary(inv)
    cost = cost_analysis.analyze(inv, ctx.config, wl, vol)
    sec = security_findings.analyze(inv, ctx.config)
    fp = platform_footprint.analyze(inv, cost)
    pbi = platform_footprint.powerbi_summary(inv)
    graph = lineage.build_graph(ctx, inv)
    gmetrics = lineage.graph_metrics(graph, inv)
    coverage = ctx.coverage.entries()
    ready = migration_readiness.analyze(inv, ctx.config, volume=vol, sql=sql, pipelines=pipes, pbi=pbi, security=sec,
                                        footprint=fp, coverage=coverage, base_dir=BASE_DIR)
    size = sizing.analyze(inv, volume=vol, workloads=wl, pipelines=pipes, pbi=pbi, security=sec, readiness=ready, footprint=fp)
    platform = {"volume": vol, "pipelines": pipes, "sql": sql, "pricing": ctx.config.get("pricing_estimates", {}),
                "window_days": wl.get("window_days")}
    focus = None
    if focus_file:
        scope = load_yaml(focus_file)
        focus = use_case.evaluate(graph, inv, scope, platform)
    candidates = use_case.poc_candidates(graph, inv, platform)
    arch = platform_footprint.architecture_mermaid(fp, inv, inv.get("powerbi") or {}, pipes)
    log.info("Análise concluída: %s tabelas, %s pipelines, %s modelos Power BI, %s nós de lineage",
             vol.get("tables_counted"), pipes.get("total"), pbi.get("semantic_models"), gmetrics.get("nodes"))
    return {"inventory": inv, "workloads": wl, "volume": vol, "sql": sql, "pipelines": pipes, "cost": cost, "security": sec,
            "footprint": fp, "powerbi": pbi, "graph": graph, "lineage": gmetrics, "readiness": ready, "sizing": size,
            "focus": focus, "poc_candidates": candidates, "architecture_mermaid": arch, "coverage": coverage,
            "missing": ctx.coverage.missing()}
