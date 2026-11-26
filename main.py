#!/usr/bin/env python3
"""GCP Data Platform Assessment — Discovery & Migration Readiness (CLI).

Exemplos:
  python main.py --preflight
  python main.py --all --days 90
  python main.py --module discovery
  python main.py --module bigquery --days 90
  python main.py --module powerbi
  python main.py --module billing
  python main.py --analyze-only --input ./output
  python main.py --projects project-a,project-b --all
  python main.py --focus-file scope.yaml --days 90
  python main.py --demo --output ./examples/demo_output
"""
from __future__ import annotations

import threading

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from core.context import Context, dump_json, load_config  # noqa: E402
from core.coverage import CoverageTracker  # noqa: E402
from utils.logging import setup_logging  # noqa: E402
from utils.redaction import Redactor  # noqa: E402

MODULES = ["discovery", "bigquery", "storage", "pipelines", "services", "security", "billing", "lineage", "powerbi"]
COVERAGE_MODULES = {"discovery": ["discovery"], "bigquery": ["bigquery", "bigquery_workloads", "bigquery_capacity"],
                    "storage": ["cloud_storage"], "pipelines": ["pipelines"], "services": ["services"], "security": ["security"],
                    "billing": ["billing"], "lineage": ["lineage"], "powerbi": ["powerbi"]}
GCP_MODULES = set(MODULES) - {"powerbi"}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Assessment somente-leitura de plataformas de dados em GCP + Power BI.")
    g = p.add_argument_group("execução")
    g.add_argument("--preflight", action="store_true", help="Valida autenticação, APIs e permissões e gera a matriz de acessos")
    g.add_argument("--all", action="store_true", help="Executa todos os módulos")
    g.add_argument("--module", action="append", default=[], help=f"Módulo(s): {', '.join(MODULES)} (repetível ou separado por vírgula)")
    g.add_argument("--analyze-only", action="store_true", help="Somente análise/relatório a partir de dados brutos já coletados")
    g.add_argument("--input", help="Pasta com dados coletados (para --analyze-only)")
    g.add_argument("--demo", action="store_true", help="Gera massa sintética e o relatório de demonstração (offline)")
    g.add_argument("--sequential", action="store_true", help="Executa os módulos um por vez (padrão: módulos independentes em paralelo)")
    g.add_argument("--print-custom-role", choices=["project", "organization"],
                   help="Imprime o YAML do papel IAM customizado de menor privilégio (sem acessar o GCP)")
    g.add_argument("--print-queries", action="store_true", help="Gera docs/SQL_QUERIES.md com todas as consultas usadas (sem acessar o GCP)")
    s = p.add_argument_group("escopo")
    s.add_argument("--config", default=None, help="Arquivo de configuração (YAML)")
    s.add_argument("--output", default=str(BASE / "output"), help="Pasta de saída")
    s.add_argument("--days", type=int, default=None, help="Janela de histórico (7, 30, 60, 90 ... 180)")
    s.add_argument("--projects", help="Lista de projetos separados por vírgula")
    s.add_argument("--exclude-projects", help="Projetos a excluir (vírgula)")
    s.add_argument("--regions", help="Regiões/localizações a considerar (vírgula), ex.: US,southamerica-east1")
    s.add_argument("--org", help="ID da organização (VPC-SC / Org Policy)")
    s.add_argument("--focus-file", help="YAML de caso de uso (relatório/modelo Power BI, dataset ou tabelas)")
    s.add_argument("--powerbi-offline", help="Pasta com PBIP/TMDL/PBIT/model.bim/scanner JSON")
    c = p.add_argument_group("segurança e custo")
    c.add_argument("--no-anonymize", action="store_true", help="Não pseudonimizar principals (padrão: anonimiza)")
    c.add_argument("--include-sql", action="store_true", help="Autoriza leitura de definições SQL/DDL (sql.mode=full)")
    c.add_argument("--store-sql", action="store_true", help="Além de ler, grava o texto SQL nos arquivos brutos (exige --include-sql)")
    c.add_argument("--max-bytes-billed", type=int, help="Limite de bytes faturados por consulta de metadados")
    c.add_argument("--max-total-bytes-billed", type=int, help="Orçamento total de bytes faturados na execução")
    c.add_argument("--timeout", type=int, help="Timeout HTTP (s)")
    c.add_argument("--concurrency", type=int, help="Paralelismo por projeto/região")
    c.add_argument("--impersonate-service-account", help="Service account a impersonar (GCP)")
    c.add_argument("--log-level", default="INFO")
    return p.parse_args(argv)


def apply_overrides(cfg: dict, a) -> dict:
    if a.days:
        cfg["execution"]["days"] = a.days
    if a.projects:
        cfg["gcp"]["projects"] = [x.strip() for x in a.projects.split(",") if x.strip()]
    if a.exclude_projects:
        cfg["gcp"]["exclude_projects"] = [x.strip() for x in a.exclude_projects.split(",") if x.strip()]
    if a.regions:
        cfg["gcp"]["regions"] = [x.strip() for x in a.regions.split(",") if x.strip()]
    if a.org:
        cfg["gcp"]["organization_id"] = a.org
    if a.powerbi_offline:
        cfg["powerbi"]["offline_path"] = a.powerbi_offline
    if a.no_anonymize:
        cfg["output"]["anonymize"] = False
    if a.include_sql:
        cfg["sql"]["mode"] = "full"
    if a.store_sql:
        cfg["sql"]["store_sql_text"] = bool(a.include_sql)
    if a.max_bytes_billed:
        cfg["bigquery"]["max_bytes_billed_per_query"] = a.max_bytes_billed
    if a.max_total_bytes_billed:
        cfg["bigquery"]["max_total_bytes_billed"] = a.max_total_bytes_billed
    if a.timeout:
        cfg["execution"]["timeout_seconds"] = a.timeout
    if a.concurrency:
        cfg["execution"]["concurrency"] = a.concurrency
    if a.impersonate_service_account:
        cfg["gcp"]["impersonate_service_account"] = a.impersonate_service_account
    return cfg


def selected_modules(a) -> list[str]:
    if a.all or (not a.module and not a.preflight and not a.analyze_only and a.focus_file):
        return list(MODULES)
    mods = []
    for m in a.module:
        mods += [x.strip().lower() for x in m.split(",") if x.strip()]
    bad = [m for m in mods if m not in MODULES]
    if bad:
        raise SystemExit(f"Módulo(s) inválido(s): {bad}. Opções: {MODULES}")
    return [m for m in MODULES if m in mods]


def build_clients(ctx: Context, modules: list[str], log) -> None:
    from utils.authentication import AuthError, gcp_client, powerbi_client
    from utils.bq import BigQueryRunner
    from utils.http import HttpSettings

    ex = ctx.config["execution"]
    settings = HttpSettings(timeout=float(ex["timeout_seconds"]), max_retries=int(ex["max_retries"]),
                            max_in_flight=int(ex["max_in_flight_requests"]))
    if set(modules) & GCP_MODULES:
        try:
            ctx.gcp, ctx.identity = gcp_client(ctx.config, settings)
            bq = ctx.config["bigquery"]
            ctx.bq = BigQueryRunner(ctx.gcp, max_bytes_billed=bq["max_bytes_billed_per_query"],
                                    max_total_bytes_billed=bq["max_total_bytes_billed"], execution_project=bq.get("execution_project"))
        except AuthError as exc:
            log.error("Autenticação GCP falhou: %s", exc)
            for m in modules:
                if m in GCP_MODULES:
                    for cm in COVERAGE_MODULES[m]:
                        ctx.coverage.entry(cm, "authentication", status="Permission Denied", limitations=[str(exc)[:300]])
    if "powerbi" in modules:
        pcfg = ctx.config["powerbi"]
        import os

        if pcfg.get("enabled", True) and (pcfg.get("client_id") or os.environ.get("PBI_CLIENT_ID")):
            try:
                ctx.powerbi, _ = powerbi_client(ctx.config, settings)
            except AuthError as exc:
                log.error("Autenticação Power BI falhou: %s", exc)
                ctx.coverage.entry("powerbi", "authentication", status="Permission Denied", limitations=[str(exc)[:300]])


def run_collectors(ctx: Context, modules: list[str], log) -> None:
    from collectors import (bigquery_capacity, bigquery_metadata, bigquery_workloads, billing, cloud_storage, discovery, lineage,
                            pipelines, powerbi, security, services)

    plan = {
        "discovery": [discovery.collect], "bigquery": [bigquery_metadata.collect, bigquery_workloads.collect, bigquery_capacity.collect],
        "storage": [cloud_storage.collect], "pipelines": [pipelines.collect], "services": [services.collect],
        "security": [security.collect], "billing": [billing.collect], "lineage": [lineage.collect], "powerbi": [powerbi.collect],
    }
    if ctx.gcp is not None and set(modules) & GCP_MODULES:
        discovery.resolve_projects(ctx, with_bq_locations=True, use_cache=False)
    runnable = []
    for m in modules:
        if m in GCP_MODULES and ctx.gcp is None:
            log.warning("Módulo %s ignorado: sem autenticação GCP", m)
            continue
        for cm in COVERAGE_MODULES[m]:
            ctx.coverage.reset_module(cm)  # antes de iniciar qualquer módulo: evita corrida entre módulos concorrentes
        runnable.append(m)

    def run_module(m: str) -> None:
        for fn in plan[m]:
            name = f"{fn.__module__.split('.')[-1]}"
            log.info("▶ %s", name)
            try:
                res = fn(ctx)
                log.info("✔ %s %s", name, res)
            except Exception as exc:  # um módulo nunca derruba a execução
                log.exception("✖ %s falhou: %s", name, exc)
                ctx.coverage.limitation(COVERAGE_MODULES[m][0], "execution", f"{name}: {type(exc).__name__}: {str(exc)[:200]}")
                ctx.coverage.entry(COVERAGE_MODULES[m][0], "execution").scope_errors += 1
                ctx.coverage.finalize(COVERAGE_MODULES[m][0], "execution")
            save_coverage(ctx)

    from core.scheduler import run_modules

    parallel = bool(ctx.config["execution"].get("parallel_modules", True)) and not ctx.args.get("sequential")
    if parallel and len(runnable) > 1:
        log.info("Módulos em paralelo (dependências respeitadas): %s", ", ".join(runnable))
    run_modules(runnable, run_module, parallel=parallel)


_COVERAGE_LOCK = threading.Lock()


def save_coverage(ctx: Context) -> None:
    with _COVERAGE_LOCK:  # módulos concorrentes gravam o mesmo arquivo
        _write_coverage(ctx)


def _write_coverage(ctx: Context) -> None:
    dump_json(ctx.raw_dir / "_coverage.json", {"entries": ctx.coverage.entries(), "missing": ctx.coverage.missing()})


def load_coverage(ctx: Context) -> None:
    import json

    p = ctx.raw_dir / "_coverage.json"
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        ctx.coverage.load(d.get("entries") or [], d.get("missing") or [])


def export(ctx: Context, analysis: dict, modules: list[str], started: datetime, log) -> Path:
    from exporters import csv_exporter, excel_exporter, json_exporter, markdown_report

    out = ctx.output_dir
    tables = csv_exporter.tables_for_export(analysis)
    files = []
    for name, (rows, cols) in tables.items():
        files.append(csv_exporter.write_csv(out / name, rows, cols).name)
    files += [p.name for p in json_exporter.write_all(out, analysis)]
    if ctx.config["output"].get("excel", True):
        rd = analysis["readiness"]
        summary = [("Migration Readiness Score", f"{rd.get('score')} ({rd.get('level')})"),
                   ("Cobertura de evidências do score (%)", str(rd.get("evidence_coverage_pct"))),
                   ("Volume lógico BigQuery (TB)", str(analysis["volume"]["logical"]["TB"])),
                   ("Pipelines", str(analysis["pipelines"]["total"])),
                   ("Semantic models Power BI", str(analysis["powerbi"]["semantic_models"]))]
        x = excel_exporter.write_excel(out / "assessment_report.xlsx", tables, summary)
        if x:
            files.append(x.name)
        else:
            log.warning("openpyxl não instalado: assessment_report.xlsx não gerado")
    files += ["assessment_report.md", "collection_manifest.json", "execution.log", "raw/ (dados brutos agregados)"]
    meta = {"mode": ctx.args.get("mode"), "days": ctx.days, "modules": modules, "anonymize": ctx.redactor.anonymize, "files": files}
    report = markdown_report.render(analysis, meta)
    (out / "assessment_report.md").write_text(report, encoding="utf-8")
    from utils.permissions import matrix_markdown

    (out / "permissions_matrix.md").write_text(matrix_markdown(org_id=ctx.config["gcp"].get("organization_id") or "ORG_ID"), encoding="utf-8")
    json_exporter.write_manifest(out, ctx, started, modules)
    return out / "assessment_report.md"


def main(argv=None) -> int:
    a = parse_args(argv)
    started = datetime.now(timezone.utc)
    cfg = apply_overrides(load_config(a.config), a)
    out = Path(a.input if a.analyze_only and a.input else a.output).resolve()
    log = setup_logging(out, a.log_level)
    ctx = Context(config=cfg, output_dir=out, coverage=CoverageTracker(),
                  redactor=Redactor(anonymize=cfg["output"].get("anonymize", True), redact_hosts=cfg["output"].get("redact_hosts", True)),
                  days=int(cfg["execution"]["days"]), include_sql=bool(a.include_sql))
    modules = selected_modules(a)
    ctx.args = {"mode": "demo" if a.demo else "analyze-only" if a.analyze_only else "preflight" if a.preflight else "collect",
                "modules": modules, "days": ctx.days, "projects": cfg["gcp"].get("projects"), "regions": cfg["gcp"].get("regions"),
                "include_sql": a.include_sql, "focus_file": a.focus_file, "config": a.config, "sequential": a.sequential}

    if a.print_custom_role:
        from utils.permissions import custom_role_yaml

        print(custom_role_yaml(a.print_custom_role))
        return 0

    if a.print_queries:
        from demo.query_catalog import write_query_catalog

        path = write_query_catalog(BASE / "docs" / "SQL_QUERIES.md", cfg)
        log.info("Catálogo de consultas gerado em %s", path)
        return 0

    if a.demo:
        from demo.synthetic import generate

        generate(ctx)
        ctx.args["mode"] = "demo"
        modules = list(MODULES)
    elif a.preflight:
        from utils.permissions import matrix_markdown, preflight, preflight_markdown

        build_clients(ctx, modules or list(MODULES), log)
        rep = preflight(ctx, modules or list(MODULES))
        (out / "preflight.md").write_text(preflight_markdown(rep), encoding="utf-8")
        (out / "permissions_matrix.md").write_text(matrix_markdown(org_id=cfg["gcp"].get("organization_id") or "ORG_ID"), encoding="utf-8")
        dump_json(out / "missing_permissions.json", rep["missing"])
        dump_json(out / "preflight.json", rep)
        log.info("Preflight concluído: %s/preflight.md (%s pendências)", out, len(rep["missing"]))
        return 0
    elif a.analyze_only:
        if not (out / "raw").exists():
            raise SystemExit(f"Nenhum dado bruto em {out}/raw. Execute uma coleta primeiro.")
        load_coverage(ctx)
        ctx.projects = ctx.load_raw("discovery", "projects") or []
    else:
        if not modules:
            raise SystemExit("Informe --all, --module, --preflight, --analyze-only ou --demo (veja --help).")
        load_coverage(ctx)  # execuções incrementais no mesmo output
        build_clients(ctx, modules, log)
        run_collectors(ctx, modules, log)
        save_coverage(ctx)

    from core.analysis import run as run_analysis

    analysis = run_analysis(ctx, a.focus_file)
    report = export(ctx, analysis, modules, started, log)
    log.info("Relatório: %s", report)
    if ctx.bq is not None:
        st = ctx.bq.stats
        log.info("BigQuery: %s consultas, %s dry runs, %s bloqueadas por custo, %.3f GiB faturados",
                 st.queries, st.dry_runs, st.skipped_cost, st.bytes_billed / 1024 ** 3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
