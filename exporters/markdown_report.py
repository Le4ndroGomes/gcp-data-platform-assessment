"""Relatório final em Markdown (assessment_report.md).

Arquivo único, autocontido (sem dependências externas), com diagramas Mermaid
renderizáveis no GitHub/GitLab/VS Code/Confluence. Seções conforme o escopo:
Executive Summary, Architecture AS-IS, Platform Footprint, Data Volume,
Workload Distribution, Pipeline Complexity, Power BI Footprint, Cost Breakdown,
Security & Governance, Migration Readiness, Sizing Inputs, Risks &
Recommendations e Assessment Coverage, além de apêndices de metodologia.
"""
from __future__ import annotations

from datetime import datetime, timezone

from analyzers.sql_complexity import CRITERIA_MD

NA = "—"
NB = "n/d (sem visibilidade)"


def blind(cov: list, module: str, component: str) -> bool:
    """True quando o componente não foi coletado de fato (negado, indisponível, não avaliado ou ausente).

    Evita exibir 0 onde o correto é 'sem visibilidade': ausência de dados não é ausência de recursos."""
    for c in cov:
        if c["module"] == module and c["component"] == component:
            return c["status"] in ("Permission Denied", "Not Available", "Not Assessed")
    return True


# ------------------------------------------------------------------ format
from utils.fmt import b, n  # noqa: E402


def pct(v, dec: int = 1) -> str:
    return NA if v is None else f"{n(v, dec)}%"


def ratio(v, dec: int = 1) -> str:
    return NA if v is None else f"{n(100 * v, dec)}%"


def ms(v) -> str:
    if v is None:
        return NA
    v = float(v)
    if v < 1000:
        return f"{n(v)} ms"
    if v < 60_000:
        return f"{n(v / 1000, 1)} s"
    if v < 3_600_000:
        return f"{n(v / 60_000, 1)} min"
    return f"{n(v / 3_600_000, 2)} h"


def secs(v) -> str:
    return NA if v is None else ms(float(v) * 1000)


def money(v, cur: str | None = "") -> str:
    return NA if v is None else f"{cur or ''} {n(v, 2)}".strip()


def esc(v) -> str:
    if v is None:
        return NA
    if isinstance(v, (list, tuple, set)):
        v = ", ".join(str(x) for x in v) or NA
    return str(v).replace("|", "\\|").replace("\n", " ")


def table(headers: list[str], rows: list[list], empty: str = "_Sem dados._") -> str:
    if not rows:
        return empty + "\n"
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(esc(c) if not isinstance(c, str) else c.replace("|", "\\|").replace("\n", " ") for c in r) + " |")
    return "\n".join(out) + "\n"


def bar(v, vmax, width: int = 24) -> str:
    if not vmax or v is None:
        return ""
    k = int(round(width * float(v) / float(vmax)))
    return "█" * k + "░" * (width - k)


def callout(kind: str, text: str) -> str:
    return f"> [!{kind}]\n> {text}\n"


# ---------------------------------------------------------- recommendations
def risks_and_recommendations(a: dict) -> list[dict]:
    r: list[dict] = []
    cov = a["coverage"]
    denied = [c for c in cov if c["status"] in ("Permission Denied", "Not Available")]
    if denied:
        r.append({"type": "Risco", "area": "Visibilidade", "priority": "Alta",
                  "text": f"{len(denied)} componentes sem visibilidade (permissão/API). Números desses componentes são limite inferior.",
                  "action": "Conceder as permissões de missing_permissions.json e reexecutar os módulos afetados."})
    for reg in (a["readiness"].get("regions") or {}).get("regions") or []:
        miss = set(reg.get("missing_features") or [])
        important = [f for f in ("lakeflow_connect_managed", "model_serving_cpu", "fmapi_pay_per_token", "databricks_apps", "lakebase") if f in miss]
        if not reg.get("supported"):
            r.append({"type": "Risco", "area": "Região", "priority": "Alta",
                      "text": f"Região {reg['databricks_region']} (dados em {reg['gcp_location']}) não suportada pelo Databricks on GCP.",
                      "action": "Definir região Databricks suportada e validar residência de dados (LGPD)."})
        elif important:
            r.append({"type": "Risco", "area": "Região", "priority": "Alta" if "lakeflow_connect_managed" in important else "Média",
                      "text": f"Em {reg['databricks_region']} não estão disponíveis: {', '.join(important)} (snapshot {a['readiness']['regions'].get('snapshot_date')}).",
                      "action": "Planejar alternativas (ex.: Datastream→GCS→Auto Loader no lugar de Lakeflow Connect; serving em outra região com aprovação jurídica) e revalidar a disponibilidade."})
    sql = a["sql"]
    dist = {}
    for d in (sql.get("distribution") or {}).values():
        for k, v in d.items():
            dist[k] = dist.get(k, 0) + v
    tot = sum(dist.values())
    if tot and (dist.get("Alta", 0) + dist.get("Muito alta", 0)) / tot > 0.2:
        r.append({"type": "Risco", "area": "SQL", "priority": "Alta",
                  "text": f"{dist.get('Alta', 0) + dist.get('Muito alta', 0)} de {tot} objetos SQL com complexidade Alta/Muito alta.",
                  "action": "Tradução assistida (Lakebridge/SQLGlot) + testes de paridade automatizados; priorizar objetos consumidos pelo BI."})
    if sql.get("remote_functions") or any(k not in ("SQL", "", None) for k in (sql.get("routine_languages") or {})):
        r.append({"type": "Risco", "area": "SQL", "priority": "Média", "text": "Rotinas JavaScript/remote functions exigem reescrita.",
                  "action": "Reescrever como UDFs Python/SQL no Unity Catalog ou mover lógica para pipelines."})
    if sql.get("confirmed_anti_patterns"):
        top = ", ".join(f"{k} ({v})" for k, v in list(sql["confirmed_anti_patterns"].items())[:3])
        r.append({"type": "Oportunidade", "area": "Performance", "priority": "Média", "text": f"Anti-patterns confirmados por execução: {top}.",
                  "action": "Corrigir na migração (Liquid Clustering, filtros de partição, materializações)."})
    vol = a["volume"]
    if vol.get("unpartitioned_large_count"):
        r.append({"type": "Oportunidade", "area": "Modelagem", "priority": "Média",
                  "text": f"{vol['unpartitioned_large_count']} tabelas ≥ 1 GiB sem particionamento.",
                  "action": "Aplicar Liquid Clustering nas tabelas Delta de destino com base nos filtros observados."})
    low = vol.get("low_usage") or {}
    if low.get("count"):
        r.append({"type": "Oportunidade", "area": "Escopo", "priority": "Média",
                  "text": f"{low['count']} tabelas ({b(low.get('bytes'))}) sem evidência de uso na janela.",
                  "action": "Validar com donos de dados e excluir do escopo (arquivar) para reduzir esforço e custo."})
    pbi = a["powerbi"]
    if pbi.get("push_down_candidates"):
        r.append({"type": "Oportunidade", "area": "Power BI", "priority": "Alta",
                  "text": f"{len(pbi['push_down_candidates'])} modelos com transformações pesadas no Power Query/tabelas calculadas.",
                  "action": "Mover a lógica para tabelas gold/metric views no Databricks; viabiliza DirectQuery e AI/BI/Genie."})
    if pbi.get("refresh_bottlenecks"):
        r.append({"type": "Risco", "area": "Power BI", "priority": "Média", "text": f"{len(pbi['refresh_bottlenecks'])} modelos com refresh > 30 min.",
                  "action": "Avaliar DirectQuery/Dual sobre Databricks SQL ou incremental refresh."})
    if pbi.get("mode") == "not_assessed":
        r.append({"type": "Risco", "area": "Power BI", "priority": "Alta", "text": "Power BI não avaliado (sem credenciais nem artefatos offline).",
                  "action": "Executar com service principal (read-only admin APIs) ou fornecer PBIP/PBIT."})
    sec = a["security"]["summary"]
    if (sec.get("by_severity") or {}).get("Alta"):
        r.append({"type": "Risco", "area": "Segurança", "priority": "Alta", "text": f"{sec['by_severity']['Alta']} achados de severidade Alta.",
                  "action": "Tratar antes/durante a fundação (Onda 0)."})
    if sec.get("sensitive_name_columns"):
        r.append({"type": "Risco", "area": "LGPD", "priority": "Alta",
                  "text": f"{sec['sensitive_name_columns']} colunas com nomes indicativos de dados pessoais/saúde (não verificados).",
                  "action": "Classificar formalmente; mapear para tags, column masks e row filters no Unity Catalog antes da carga."})
    pipes = a["pipelines"]
    if pipes.get("unstable"):
        r.append({"type": "Risco", "area": "Pipelines", "priority": "Média", "text": f"{len(pipes['unstable'])} pipelines com falha > 10%.",
                  "action": "Estabilizar ou redesenhar; não migrar instabilidade."})
    if pipes.get("external_dependencies"):
        r.append({"type": "Risco", "area": "Dependências", "priority": "Média",
                  "text": f"{len(pipes['external_dependencies'])} origens externas a investigar (fora do GCP).",
                  "action": "Mapear conectividade, CDC e donos de cada origem."})
    for o in pipes.get("opportunities") or []:
        r.append({"type": "Oportunidade", "area": "Pipelines", "priority": "Média", "text": o, "action": "Ver plano de ondas."})
    for u in a["cost"].get("underutilization") or []:
        r.append({"type": "Oportunidade", "area": "FinOps", "priority": "Baixa", "text": f"{u['resource']}: {u['evidence']}",
                  "action": "Revisar dimensionamento/retenção."})
    order = {"Alta": 0, "Média": 1, "Baixa": 2}
    return sorted(r, key=lambda x: (order[x["priority"]], x["type"]))


# ------------------------------------------------------------------ report
def render(a: dict, meta: dict) -> str:
    inv, wl, vol, sql, pipes, cost, sec, fp, pbi, rd, sz = (a["inventory"], a["workloads"], a["volume"], a["sql"], a["pipelines"],
                                                           a["cost"], a["security"], a["footprint"], a["powerbi"], a["readiness"], a["sizing"])
    cov = a["coverage"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    billed = cost.get("billed")
    cur = (billed or {}).get("currency") or (cost.get("estimated") or {}).get("currency")
    in_use = [s for s in fp["services"] if s["status"].startswith("Em uso")]
    provisioned = [s for s in fp["services"] if s["status"].startswith("Provisionado")]
    statuses = {}
    for c in cov:
        statuses[c["status"]] = statuses.get(c["status"], 0) + 1
    complete_pct = round(100 * (statuses.get("Complete", 0) + 0.5 * statuses.get("Partial", 0)) / len(cov), 1) if cov else 0
    recs = risks_and_recommendations(a)
    L: list[str] = []
    add = L.append

    add("# GCP Data Platform Assessment — Discovery & Migration Readiness\n")
    add(table(["Item", "Valor"], [
        ["Gerado em", now], ["Modo", meta.get("mode")], ["Janela de workloads", f"{wl.get('window_days') or meta.get('days')} dias"],
        ["Projetos avaliados", f"{fp['projects_selected']} de {fp['projects']}"], ["Módulos", ", ".join(meta.get("modules") or [])],
        ["Modo SQL", sql.get("sql_mode") or NA], ["Anonimização", "ativada" if meta.get("anonymize") else "desativada"],
        ["Cobertura da coleta", f"{n(complete_pct, 1)}% (Complete=1, Partial=0,5 sobre {len(cov)} componentes)"],
    ]))
    add(callout("NOTE", "Relatório gerado a partir de metadados, históricos de jobs e configurações (somente leitura). "
                        "Nenhum dado de negócio foi lido. Ausência de dados em componentes sem permissão **não** significa ausência de recursos — ver *Assessment Coverage*."))
    toc = ["Executive Summary", "Architecture AS-IS", "Platform Footprint", "Data Volume", "Workload Distribution", "Pipeline Complexity",
           "Power BI Footprint", "Cost Breakdown", "Security & Governance", "Migration Readiness", "Sizing Inputs",
           "Risks & Recommendations", "Assessment Coverage", "Apêndices"]
    add("## Sumário\n")
    add("\n".join(f"{i}. [{t}](#{i}-{t.lower().replace(' ', '-').replace('&', '')})" for i, t in enumerate(toc, 1)) + "\n")

    # 1 ---------------------------------------------------------------
    add("## 1. Executive Summary\n")
    monthly = billed.get("monthly_total_last_full") if billed else None
    est = cost.get("estimated") or {}
    est_total = sum(est.get(k) or 0 for k in ("bq_on_demand_analysis_month", "bq_storage_month", "bq_reservation_baseline_month", "gcs_storage_month"))
    kpis = [
        ["Serviços de dados com evidência de uso", f"{len(in_use)} (+{len(provisioned)} provisionados sem uso)"],
        ["Volume BigQuery (lógico / físico)", f"{b(vol['logical']['bytes'])} / {b(vol['physical']['bytes'])}"],
        ["Tabelas físicas / views / rotinas", f"{n(vol.get('tables_counted'))} / {n(len(inv.get('views') or []))} / {n(len(inv.get('routines') or []))}"],
        ["Registros (metadados)", n(vol.get("total_rows"))],
        ["Cloud Storage", (f"{n(vol['gcs']['buckets'])} buckets; volume {NB}" if blind(cov, "cloud_storage", "volume_metrics")
                           else f"{b(vol['gcs']['total']['bytes'])} em {n(vol['gcs']['buckets'])} buckets")],
        ["Jobs BigQuery por dia (média)", n((wl.get("totals") or {}).get("jobs_per_day"), 1)],
        ["Slots BigQuery (média / pico por minuto)", f"{n((wl.get('slots') or {}).get('avg_slots_window'), 1)} / {n((wl.get('slots') or {}).get('peak_minute_slots'))}"],
        ["Pipelines identificados", f"{n(pipes.get('total'))} ({n(pipes.get('streaming'))} streaming)"],
        ["Power BI (modelos / relatórios / sobre BigQuery)", f"{n(pbi.get('semantic_models'))} / {n(pbi.get('reports'))} / {n(pbi.get('models_using_bigquery'))}"],
        ["Custo mensal", f"{money(monthly, cur)} (faturado, {billed['last_full_month']})" if billed else f"{money(est_total, est.get('currency'))} (**ESTIMATIVA** parcial: BigQuery + GCS)"],
        ["Migration Readiness Score", f"**{n(rd.get('score'), 1)}/100** — {rd.get('level') or NA} (cobertura de evidências {pct(rd.get('evidence_coverage_pct'))})"],
    ]
    add(table(["Indicador", "Valor"], kpis))
    add("**Principais achados**\n")
    highlights = []
    if in_use:
        highlights.append("Plataforma além de BigQuery/Power BI: " + ", ".join(s["service"] for s in in_use if s["key"] != "bigquery"))
    if wl.get("classes"):
        top_cls = max(wl["classes"], key=lambda c: c["slot_pct"])
        highlights.append(f"Workload dominante por slots: **{top_cls['class']}** ({pct(top_cls['slot_pct'])} dos slot-hours)")
    if vol.get("growth") and isinstance(vol["growth"].get(90), dict):
        highlights.append(f"Crescimento do armazenamento BigQuery em 90 dias: {pct(vol['growth'][90]['pct'])}")
    if pbi.get("storage_modes"):
        highlights.append("Modos de armazenamento Power BI: " + ", ".join(f"{k} ({v})" for k, v in pbi["storage_modes"].items()))
    if sec["summary"].get("by_severity"):
        highlights.append("Achados de segurança: " + ", ".join(f"{k}: {v}" for k, v in sec["summary"]["by_severity"].items()))
    add("\n".join(f"- {h}" for h in highlights) + "\n" if highlights else "_Sem achados suficientes._\n")
    add("**Riscos e recomendações prioritários**\n")
    add("\n".join(f"- **[{x['type']} · {x['area']}]** {x['text']} → {x['action']}" for x in recs[:6]) + "\n" if recs else "_Nenhum._\n")
    if a.get("poc_candidates"):
        add("**Candidatos a POC de modernização** (ver detalhes no item 12 e no apêndice)\n")
        add(table(["Modelo Power BI", "Modo", "Tabelas", "Volume", "Pipelines", "Complexidade SQL", "Visualizações", "Fit"],
                  [[c["name"], c.get("storage_mode"), n(c["tables"]), b(c["logical_bytes"]), n(c["pipelines"]), c.get("worst_sql_complexity") or NA,
                    n(c.get("report_views")), n(c["poc_fit_score"], 1)] for c in a["poc_candidates"]]))

    # 2 ---------------------------------------------------------------
    add("## 2. Architecture AS-IS\n")
    add("Diagrama gerado a partir dos serviços com evidência (linha cheia = fluxo observado/configurado; tracejada = origem externa a investigar).\n")
    add("```mermaid\n" + a["architecture_mermaid"] + "\n```\n")
    lm = a["lineage"]
    add(f"Grafo de lineage: **{n(lm['nodes'])} nós / {n(lm['edges'])} arestas** "
        f"({', '.join(f'{k}: {v}' for k, v in (lm.get('edge_status') or {}).items())}); profundidade máxima **{lm.get('max_depth')}**; "
        f"{lm.get('cycle_count')} dependências circulares. Arquivos: `data_lineage.json`, `data_lineage.graphml`, `data_lineage.mmd`.\n")

    # 3 ---------------------------------------------------------------
    add("## 3. Platform Footprint\n")
    add(table(["Organizações", "Folders", "Projetos (selecionados)", "Billing accounts", "Service accounts", "Ambientes", "Localizações BigQuery"],
              [[n(fp["organizations"]), n(fp["folders"]), f"{n(fp['projects'])} ({n(fp['projects_selected'])})", NB if blind(cov, "discovery", "billing_accounts") else n(fp["billing_accounts"]),
                NB if blind(cov, "discovery", "service_accounts") else n(fp["service_accounts"]), ", ".join(f"{k}: {v}" for k, v in fp["environments"].items()) or NA,
                ", ".join(f"{k}: {v}" for k, v in fp["locations"].items()) or NA]]))
    add("### Serviços: habilitado × provisionado × uso efetivo\n")
    add(table(["Serviço", "Status", "Projetos c/ API", "Recursos", "Evidência de uso", "Destino Databricks (hipótese)"],
              [[s["service"], s["status"], n(s["enabled_projects"]), n(s["provisioned"]), s["usage_evidence"], s["databricks_target"]]
               for s in sorted(fp["services"], key=lambda s: (["Em uso (evidência)", "Provisionado sem evidência de uso",
                                                                "Habilitado sem recursos encontrados", "Não habilitado", "Sem visibilidade"].index(s["status"]), s["service"]))]))
    net = fp.get("network") or {}
    if net and blind(cov, "discovery", "network"):
        add("### Rede e conectividade\n")
        add(callout("WARNING", "Rede (VPC, VPN, Interconnect, NAT) sem visibilidade nesta execução: os dados não foram coletados. "
                               "Isso não significa ausência de recursos de rede."))
    elif net:
        add("### Rede e conectividade\n")
        add(table(["VPCs", "Subnets (PGA)", "Peerings", "VPN", "Interconnect", "Cloud NAT", "Shared VPC host/service", "Private Services Access"],
                  [[n(net.get("vpcs")), f"{n(net.get('subnets'))} ({n(net.get('subnets_private_google_access'))})", n(net.get("peerings")),
                    n(net.get("vpn_tunnels")), n(net.get("interconnect_attachments")), n(net.get("cloud_nat")),
                    f"{n(net.get('shared_vpc_hosts'))}/{n(net.get('shared_vpc_service_projects'))}", n(net.get("private_services_access"))]]))
    if fp.get("other_enabled_apis"):
        add("<details><summary>Outras APIs habilitadas (fora do catálogo)</summary>\n\n" +
            table(["API", "Projetos"], [[k, n(v)] for k, v in fp["other_enabled_apis"].items()]) + "\n</details>\n")

    # 4 ---------------------------------------------------------------
    add("## 4. Data Volume\n")
    add(table(["Métrica", "GB", "TB", "TiB"], [[lbl, n(vol[k]["GB"], 2), n(vol[k]["TB"], 3), n(vol[k]["TiB"], 3)] for lbl, k in [
        ("Lógico total", "logical"), ("Lógico ativo", "active_logical"), ("Lógico long-term", "long_term_logical"),
        ("Físico total", "physical"), ("Físico ativo", "active_physical"), ("Físico long-term", "long_term_physical"),
        ("Time travel (físico)", "time_travel_physical"), ("Fail-safe (físico)", "fail_safe_physical"),
        ("Snapshots/clones (físico, separado)", "snapshot_clone_physical")]]))
    add(f"Tabelas contabilizadas: {n(vol['tables_counted'])} (BASE TABLE + MATERIALIZED VIEW); snapshots/clones: {n(vol['snapshots_clones'])} "
        f"(somente bytes físicos próprios, sem dupla contagem). Taxa de compressão observada (lógico/físico): {n(vol.get('compression_ratio_observed'), 2)}.\n")
    add("### Volume por projeto\n")
    add(table(["Projeto", "Lógico", "Físico", "Tabelas"], [[p["project_id"], b(p["logical_bytes"]), b(p["physical_bytes"]), n(p["tables"])]
                                                         for p in vol["by_project"][:30]]))
    if vol.get("by_location"):
        add("### Volume por localização\n")
        add("_Base para escolher a região do Databricks e validar residência de dados._\n")
        add(table(["Localização", "Lógico", "Físico", "Tabelas"],
                  [[x["location"], b(x["logical_bytes"]), b(x["physical_bytes"]), n(x["tables"])] for x in vol["by_location"]]))
    add("### Top datasets\n")
    add(table(["Dataset", "Lógico", "Físico", "Long-term", "Tabelas", "Registros"],
              [[d["dataset"], b(d["logical_bytes"]), b(d["physical_bytes"]), b(d["long_term_bytes"]), n(d["tables"]), n(d["rows"])] for d in vol["by_dataset"][:20]]))
    add("### Top 20 maiores tabelas\n")
    add(table(["Tabela", "Tipo", "Lógico", "Físico", "Registros", "Particionada", "Clusterizada", "Jobs de leitura"],
              [[t["table"], t["type"], b(t["logical_bytes"]), b(t["physical_bytes"]), n(t["rows"]), "sim" if t["partitioned"] else "não",
                "sim" if t["clustered"] else "não", n(t["reads_jobs"])] for t in vol["top20"]]))
    sd = vol["size_distribution"]
    mx = max(sd.values() or [0])
    add("### Distribuição das tabelas por tamanho\n")
    add(table(["Faixa", "Tabelas", ""], [[k, n(v), f"`{bar(v, mx)}`"] for k, v in sd.items()]))
    add(f"**Tabelas ≥ 1 GiB sem particionamento:** {n(vol['unpartitioned_large_count'])}. ")
    lu = vol["low_usage"]
    add(f"**Baixa utilização:** {n(lu['count']) if lu['available'] else 'não avaliado (sem JOBS)'} tabelas ({b(lu.get('bytes'))}) — critério: {lu['criteria']}.\n")
    g = vol["growth"]
    add("### Crescimento histórico (BigQuery, Cloud Monitoring)\n")
    if g.get("coverage_days"):
        add(table(["Janela", "Variação", "%", "Média diária"],
                  [[f"{w} dias", b(g[w]["delta_bytes"]) if isinstance(g.get(w), dict) else "histórico insuficiente",
                    pct(g[w]["pct"]) if isinstance(g.get(w), dict) else NA,
                    b(g[w]["avg_daily_delta_bytes"]) if isinstance(g.get(w), dict) else NA] for w in (30, 60, 90, 180)]))
        add(f"Série disponível: {g.get('first_day')} → {g.get('last_day')} ({g.get('coverage_days')} dias).\n")
    else:
        add("_Sem série histórica disponível; crescimento não estimado (nenhum valor inventado)._\n")
    ing = vol["ingestion"]
    add(table(["Evidência de ingestão diária", "Valor", "Fonte"], [
        ["Streaming (Storage Write/insertAll)", b(ing.get("streaming_bytes_per_day")), ing.get("streaming_source") or NA],
        ["Crescimento líquido/dia (30d)", b(ing.get("net_growth_bytes_per_day")), "stored_bytes"],
        ["Partições modificadas/dia (proxy)", b(ing.get("modified_partitions_bytes_per_day")), ing.get("modified_partitions_note") or "PARTITIONS desabilitado"]]))
    im = vol["initial_migration"]
    add(f"**Volume estimado para migração inicial:** {b(im['logical']['bytes'])} lógico / {b(im['physical']['bytes'])} físico "
        f"({b(im['excluding_low_usage_logical']['bytes'])} lógico excluindo baixa utilização). {im['note']}\n")
    add(f"**Potencial incremental:** {n(vol['incremental']['tables_with_writes'])} tabelas com escrita na janela, somando {b(vol['incremental']['bytes_of_tables_with_writes'])}.\n")
    gcs = vol["gcs"]
    add("### Cloud Storage\n")
    add(table(["Buckets", "Volume", "Objetos", "Por classe", "Formatos (amostra)", "Small files (amostra)"],
              [[n(gcs["buckets"]), NB if blind(cov, "cloud_storage", "volume_metrics") else b(gcs["total"]["bytes"]),
                NB if blind(cov, "cloud_storage", "volume_metrics") else n(gcs["objects"]),
                ", ".join(f"{k}: {b(v['bytes'])}" for k, v in gcs["by_storage_class"].items()) or NA,
                ", ".join(f"{k} ({v})" for k, v in list(gcs["formats_sampled"].items())[:8]) or NA, ratio(gcs.get("small_file_ratio_sampled"))]]))
    bk = sorted(inv.get("buckets") or [], key=lambda x: -(x.get("total_bytes") or 0))[:15]
    add(table(["Bucket", "Local", "Classe", "Volume", "Objetos", "Lifecycle", "Versionamento", "CMEK", "Formatos dominantes"],
              [[x["bucket"], x.get("location"), x.get("storage_class"), b(x.get("total_bytes")), n(x.get("object_count")),
                n(x.get("lifecycle_rules")), "sim" if x.get("versioning") else "não", "sim" if x.get("cmek") else "não",
                ", ".join(list(((x.get("sample") or {}).get("formats") or {}).keys())[:3]) or NA] for x in bk]))

    # 5 ---------------------------------------------------------------
    add("## 5. Workload Distribution\n")
    if not wl.get("available"):
        add(callout("WARNING", "Histórico de jobs indisponível (INFORMATION_SCHEMA.JOBS sem acesso ou módulo não executado)."))
    else:
        t = wl["totals"]
        add(table(["Métrica", "Valor"], [
            ["Jobs (lógicos, scripts contam 1×)", n(t["jobs"])], ["Statements (excl. pai de script)", n(t["statements"])],
            ["Scripts / child jobs", f"{n(t['scripts'])} / {n(t['child_jobs'])}"], ["Jobs por dia", n(t["jobs_per_day"], 1)],
            ["Falhas", f"{n(t['failed'])} ({ratio(t['failure_rate'], 2)})"], ["Cache hit", ratio(t["cache_hit_rate"])],
            ["Bytes processados / faturados", f"{b(t['bytes_processed'])} / {b(t['bytes_billed'])}"],
            ["Bytes faturados on-demand (sem reserva)", b(t["on_demand_bytes_billed"])],
            ["Slot-hours", n(wl["slots"]["slot_hours"], 1)], ["Duração média / máxima", f"{ms(t['avg_run_ms'])} / {ms(t['max_run_ms'])}"],
            ["Espera média em fila", ms(t["avg_queue_ms"])]]))
        pe = wl["percentiles_estimated"]
        add("**Latência (jobs de topo)** — estimativa por histograma mesclado: "
            f"P50 {ms(pe['p50_ms'])} · P90 {ms(pe['p90_ms'])} · P95 {ms(pe['p95_ms'])} · P99 {ms(pe['p99_ms'])}; fila P95 {ms(pe['queue_p95_ms'])}.\n")
        add(table(["Projeto", "Região", "Tipo", "Prioridade", "Jobs", "P50", "P90", "P95", "P99"],
                  [[p["project_id"], p["location"], p["job_type"], p["priority"], n(p["jobs"]), ms(p.get("p50_ms")), ms(p.get("p90_ms")),
                    ms(p.get("p95_ms")), ms(p.get("p99_ms"))] for p in sorted(wl["percentiles_exact"], key=lambda x: -(x.get("jobs") or 0))[:15]]))
        add("### Por tipo de job e prioridade\n")
        add(table(["Tipo", "Prioridade", "Jobs", "Bytes faturados", "Slot-hours", "Falhas", "Duração média"],
                  [[x["job_type"], x["priority"], n(x["jobs"]), b(x["bytes_billed"]), n(x["slot_hours"], 1), n(x["failed"]), ms(x["avg_run_ms"])] for x in wl["by_type"]]))
        add("### Classificação dos workloads\n")
        add(table(["Classe", "Principals", "Jobs", "% jobs", "Slot-hours", "% slots"],
                  [[c["class"], n(c["principals"]), n(c["jobs"]), pct(c["jobs_pct"]), n(c["slot_hours"], 1), pct(c["slot_pct"])] for c in wl["classes"]]))
        add("Regras de classificação (ordem de precedência e confiança) no apêndice A.3.\n")
        add("### Distribuição temporal (fuso " + str(wl["seasonality"]["timezone"]) + ")\n")
        hp = wl["hour_profile"]
        mxj = max((h["avg_jobs"] for h in hp), default=0)
        slot_prof = wl["slots"].get("profile_by_hour") or {}
        add(table(["Hora", "Jobs/dia (média)", "", "Slots médios"], [[f"{h['hour']:02d}h", n(h["avg_jobs"], 1), f"`{bar(h['avg_jobs'], mxj)}`",
                                                                     n(slot_prof.get(h["hour"]) if slot_prof.get(h["hour"]) is not None else slot_prof.get(str(h["hour"])), 1)] for h in hp]))
        dp = wl["dow_profile"]
        mxd = max((d["jobs"] for d in dp), default=0)
        add(table(["Dia", "Jobs", ""], [[d["dow"], n(d["jobs"]), f"`{bar(d['jobs'], mxd)}`"] for d in dp]))
        s = wl["seasonality"]
        add(f"Sazonalidade: CV diário {n(s['daily_cv'], 2)} · fim de semana/dia útil {n(s['weekend_vs_weekday'], 2)} · "
            f"virada de mês/meio do mês {n(s['month_edge_vs_mid'], 2)} · hora de pico {s['peak_hour_local']}h · dia de pico {s['peak_day']}.\n")
        sl = wl["slots"]
        add(table(["Slots médios (janela)", "Slots médios (horas ativas)", "P95 horário", "Pico (minuto)", "Concorrência máx.", "Fonte"],
                  [[n(sl["avg_slots_window"], 1), n(sl["avg_slots_active_hours"], 1), n(sl["p95_hourly_avg_slots"], 1), n(sl["peak_minute_slots"]),
                    n(sl["peak_concurrent_jobs"]), sl["source"]]]))
        add(f"_{sl['note']}_\n")
        fs = wl["fingerprint_summary"]
        add(f"### Consultas recorrentes e top consultas\n\nFingerprints distintos: {n(fs.get('distinct_fingerprints'))}; recorrentes (>1 execução): "
            f"{n(fs.get('recurring_fingerprints'))}; presentes em ≥7 dias: {n(fs.get('fingerprints_7plus_days'))}; execuções de recorrentes: "
            f"{n(fs.get('runs_of_recurring'))} de {n(fs.get('query_statements'))}.\n")
        for title, key, col in (("Top por custo (bytes faturados)", "top_by_cost", "bytes_billed"), ("Top por slots", "top_by_slots", "slot_ms"),
                                ("Top por duração média", "top_by_duration", "avg_run_ms")):
            add(f"**{title}**\n")
            add(table(["Fingerprint", "Projeto", "Tipo", "Execuções", "Bytes faturados", "Slot-hours", "Duração média", "Tabelas"],
                      [[f"`{str(f.get('fp'))[:12]}`", f["project_id"], f.get("statement_type"), n(f.get("runs")), b(f.get("bytes_billed")),
                        n((f.get("slot_ms") or 0) / 3.6e6, 2), ms(f.get("avg_run_ms")), ", ".join((f.get("refs") or [])[:3])] for f in wl[key][:10]]))
        add("### Tabelas mais acessadas\n")
        add(table(["Tabela", "Jobs", "Principals", "Principals de BI", "Última leitura"],
                  [[x["table_ref"], n(x["jobs"]), n(x["principals"]), n(x["bi_principals"]), (x.get("last_read") or "")[:10]] for x in wl["top_tables"][:20]]))
        pe2 = wl["powerbi_evidence"]
        add(f"**Evidência de consumo pelo Power BI:** {n(pe2['principals'])} principals, {n(pe2['jobs'])} jobs, {n(pe2['slot_hours'], 1)} slot-hours, "
            f"{b(pe2['bytes_billed'])} faturados. _{pe2['note']}_\n")
        top_c = wl["consumers"][:15]
        add(table(["Principal (pseudônimo)", "Classe", "Confiança", "Jobs", "Slot-hours", "Bytes faturados"],
                  [[c["principal"], c["workload_class"], c["class_confidence"], n(c.get("jobs")), n((c.get("slot_ms") or 0) / 3.6e6, 1),
                    b(c.get("bytes_billed"))] for c in top_c]))

    # 6 ---------------------------------------------------------------
    add("## 6. Pipeline Complexity\n")
    add(table(["Total", "Streaming", "Execuções/dia (estimado)", "Por tecnologia"], [[n(pipes["total"]), n(pipes["streaming"]), n(pipes["runs_per_day_total"], 1),
                                                                                    ", ".join(f"{k}: {v}" for k, v in pipes["by_technology"].items()) or NA]]))
    add(table(["Dimensão", "Distribuição"], [["Frequência", ", ".join(f"{k}: {v}" for k, v in pipes["by_frequency"].items()) or NA],
                                             ["Estratégia de carga", ", ".join(f"{k}: {v}" for k, v in pipes["by_load_strategy"].items()) or NA],
                                             ["Processamento", ", ".join(f"{k}: {v}" for k, v in pipes["by_processing"].items()) or NA],
                                             ["Complexidade de migração", ", ".join(f"{k}: {v}" for k, v in pipes["by_complexity"].items()) or NA]]))
    plist = sorted(inv.get("pipelines") or [], key=lambda p: (-(p.get("runs_per_day") or 0)))[:30]
    add("### Inventário (amostra — ver `pipeline_inventory.csv`)\n")
    add(table(["Pipeline", "Tecnologia", "Origem", "Destino", "Frequência", "Estratégia", "Duração média/máx", "Falhas", "Complexidade"],
              [[p["name"], p["technology"], ", ".join((p.get("sources") or [])[:2]) or NA, ", ".join((p.get("targets") or [])[:2]) or NA,
                p.get("frequency"), p.get("load_strategy"), f"{secs(p.get('avg_duration_s'))} / {secs(p.get('max_duration_s'))}",
                ratio(p.get("failure_rate")), p.get("migration_complexity")] for p in plist]))
    if pipes["unstable"]:
        add("**Pipelines instáveis (falha > 10%)**\n")
        add(table(["Pipeline", "Tecnologia", "Taxa de falha", "Execuções"], [[u["name"], u["technology"], ratio(u["failure_rate"]), n(u["runs"])] for u in pipes["unstable"]]))
    if pipes["longest"]:
        add("**Execuções mais longas**\n")
        add(table(["Pipeline", "Tecnologia", "Máxima", "Média"], [[x["name"], x["technology"], secs(x["max_duration_s"]), secs(x["avg_duration_s"])] for x in pipes["longest"][:10]]))
    if pipes["external_dependencies"]:
        add("**Dependências externas a investigar:** " + ", ".join(f"`{x}`" for x in pipes["external_dependencies"]) + "\n")
    if pipes["chains"]:
        add("**Cadeias de orquestração observadas:**\n" + "\n".join(f"- {c}" for c in pipes["chains"][:15]) + "\n")
    lm = a["lineage"]
    add("### Dependências e caminhos críticos (lineage)\n")
    add(f"Caminho crítico (maior profundidade): {' → '.join(f'`{x}`' for x in lm['critical_path']) or NA}\n")
    add(table(["Possíveis single points of failure", "Tipo", "Ativos de BI dependentes", "Downstream total"],
              [[s["label"], s["kind"], n(s["downstream_bi_assets"]), n(s["downstream_total"])] for s in lm["single_points_of_failure"][:10]]))
    add(table(["Maior fan-in", "Fan-in", "Maior fan-out", "Fan-out"],
              [[x["label"], n(x["fan_in"]), y["label"], n(y["fan_out"])] for x, y in zip(lm["top_fan_in"][:8], lm["top_fan_out"][:8])]))
    add(f"Dependências circulares: {n(lm['cycle_count'])}. Objetos sem evidência de consumo: {n(lm['objects_without_consumption_evidence'])}.\n")
    add("### Complexidade de SQL e anti-patterns\n")
    add(f"Modo de análise SQL: `{sql.get('sql_mode')}` · objetos analisados: {n(sql['objects_analyzed'])}.\n")
    add(table(["Tipo de objeto", "Baixa", "Média", "Alta", "Muito alta"],
              [[k, n(v.get("Baixa", 0)), n(v.get("Média", 0)), n(v.get("Alta", 0)), n(v.get("Muito alta", 0))] for k, v in sql["distribution"].items()]))
    add(table(["Anti-pattern (estático — potencial)", "Ocorrências"], [[k, n(v)] for k, v in list(sql["static_anti_patterns"].items())[:15]]))
    add(table(["Anti-pattern (confirmado por execução)", "Ocorrências"], [[k, n(v)] for k, v in sql["confirmed_anti_patterns"].items()],
              empty="_Nenhum anti-pattern confirmado por evidência de execução._"))

    # 7 ---------------------------------------------------------------
    add("## 7. Power BI Footprint\n")
    if pbi.get("mode") in (None, "not_assessed"):
        add(callout("WARNING", "Power BI não avaliado. Use credenciais Entra ID (Scanner API) ou `powerbi.offline_path` com PBIP/PBIT."))
    else:
        add(table(["Modo", "Workspaces", "Relatórios", "Dashboards", "Dataflows", "Semantic models", "Gateways", "Viewers ativos"],
                  [[pbi["mode"], n(pbi["workspaces"]), n(pbi["reports"]), n(pbi["dashboards"]), n(pbi["dataflows"]), n(pbi["semantic_models"]),
                    n(pbi["gateways"]), f"{n(pbi.get('active_viewers'))} ({n(pbi.get('activity_window_days'))} dias)" if pbi.get("active_viewers") is not None else NA]]))
        add(table(["Indicador", "Valor"], [
            ["Modos de armazenamento", ", ".join(f"{k}: {v}" for k, v in pbi["storage_modes"].items()) or NA],
            ["Modelos sobre BigQuery / relatórios sobre BigQuery", f"{n(pbi['models_using_bigquery'])} / {n(pbi['reports_on_bigquery'])}"],
            ["Projetos BigQuery usados", ", ".join(f"{k} ({v})" for k, v in pbi["bigquery_projects_used"].items()) or NA],
            ["Datasets BigQuery usados", ", ".join(f"{k} ({v})" for k, v in list(pbi["bigquery_datasets_used"].items())[:10]) or NA],
            ["Modelos com SQL nativo", n(pbi["native_sql_models"])],
            ["Medidas DAX / colunas calculadas / tabelas calculadas", f"{n(pbi['measures'])} / {n(pbi['calculated_columns'])} / {n(pbi['calculated_tables'])}"],
            ["Modelos com RLS (dinâmico)", f"{n(pbi['rls_models'])} ({n(pbi['dynamic_rls_models'])})"],
            ["Fontes de dados por tipo", ", ".join(f"{k}: {v}" for k, v in pbi["datasources_by_type"].items()) or NA],
            ["Refresh: entradas / falhas / P50 / máx.", f"{n(pbi['refresh']['history_entries'])} / {n(pbi['refresh']['failures'])} / "
                                                         f"{secs(pbi['refresh']['p50_duration_s'])} / {secs(pbi['refresh']['max_duration_s'])}"],
            ["Capacidades", ", ".join(f"{c.get('name')} ({c.get('sku')})" for c in pbi["capacities"]) or NA]]))
        models = sorted(inv.get("pbi_models") or [], key=lambda m: -(m.get("table_count") or 0))[:25]
        add("### Semantic models\n")
        add(table(["Modelo", "Modo", "Tabelas", "Colunas calc.", "Medidas", "Relac. (bidir.)", "RLS", "Fontes", "Passos M (pesados)", "SQL nativo"],
                  [[m["name"], m.get("storage_mode"), n(m.get("table_count")), n(m.get("calculated_columns")), n(m.get("measure_count")),
                    f"{n(m.get('relationship_count'))} ({n(m.get('bidirectional_relationships'))})", "sim" if m.get("rls") else "não",
                    ", ".join(m.get("source_types") or m.get("datasource_types") or []) or NA,
                    f"{n(m.get('m_steps'))} ({n(sum((m.get('heavy_m_steps') or {}).values()))})", n(m.get("native_query_count"))] for m in models]))
        if pbi["correlations"]:
            add("### Correlação Power BI × BigQuery\n")
            add("Confiança: **high** = referência explícita no Power Query + leitura por principal Power BI ou sobreposição com janela de refresh; "
                "**medium** = apenas referência explícita ou apenas sobreposição temporal; **low** = SQL nativo sem corroboração. Inferências não são tratadas como dependências confirmadas.\n")
            add(table(["Modelo", "Tabela BigQuery", "Declaração", "Leituras PBI", "Sobreposição refresh (h)", "Confiança"],
                      [[c["model"], c["table_ref"], c["declared"], n(c["powerbi_principal_reads"]), n(c["refresh_hour_overlap"]), c["confidence"]]
                       for c in pbi["correlations"][:40]]))
        for title, key in (("Gargalos de atualização (> 30 min)", "refresh_bottlenecks"), ("Candidatos a simplificação", "simplification_candidates"),
                           ("Processamento a transferir para a camada de dados", "push_down_candidates")):
            if pbi.get(key):
                add(f"**{title}**\n")
                rows = pbi[key][:15]
                add(table(list(rows[0].keys()), [list(r.values()) for r in rows]))
        if pbi.get("redundant_report_groups") or pbi.get("redundant_model_groups"):
            add("**Potencial redundância:** " + "; ".join(" / ".join(g) for g in (pbi["redundant_model_groups"] + pbi["redundant_report_groups"])[:10]) + "\n")
        if pbi.get("offline_warnings"):
            add("**Avisos do modo offline:**\n" + "\n".join(f"- {w}" for w in pbi["offline_warnings"]) + "\n")

    # 8 ---------------------------------------------------------------
    add("## 8. Cost Breakdown\n")
    if billed:
        add(f"Fonte: **Cloud Billing Export** (custo faturado = cost + créditos) — `{billed['source']}`. Moeda: {cur}.\n")
        w3, w6, w12 = billed.get("window_3m"), billed.get("window_6m"), billed.get("window_12m")
        add(table(["Janela", "Total", "Média mensal", "Meses"], [[lbl, money(w["total"], cur), money(w["avg_month"], cur), len(w["months"])]
                                                               for lbl, w in (("3 meses", w3), ("6 meses", w6), ("12 meses", w12)) if w]))
        ev = billed["evolution"]
        mxc = max((e["net_cost"] for e in ev), default=0)
        add(table(["Mês", "Custo líquido", "Var. m/m", ""], [[e["month"] + (" (parcial)" if e["partial"] else ""), money(e["net_cost"], cur),
                                                           pct(e["mom_pct"]), f"`{bar(e['net_cost'], mxc)}`"] for e in ev]))
        add(f"### Último mês fechado ({billed['last_full_month']})\n")
        add(table(["Serviço", "Custo"], [[s["service"], money(s["net_cost"], cur)] for s in billed["by_service_last_month"][:15]]))
        add(table(["Categoria", "Custo"], [[k, money(v, cur)] for k, v in sorted(billed["by_category_last_month"].items(), key=lambda x: -x[1])]))
        add(table(["BigQuery — componente", "Custo"], [[k, money(v, cur)] for k, v in billed["bigquery_breakdown_last_month"].items()]))
        add(table(["Projeto", "Custo"], [[p["project_id"], money(p["net_cost"], cur)] for p in billed["by_project_last_month"][:15]]))
        add("### Principais cost drivers (SKU, janela)\n")
        add(table(["Serviço", "SKU", "Categoria", "Custo"], [[d["service"], d["sku"], d["category"], money(d["net_cost_window"], cur)] for d in billed["top_cost_drivers"]]))
        if billed.get("credits_by_type_window"):
            add(table(["Créditos por tipo (janela)", "Valor"], [[k, money(v, cur)] for k, v in billed["credits_by_type_window"].items()]))
    else:
        add(callout("WARNING", "Billing Export não disponível/acessível: **não há custos faturados** neste relatório. Abaixo, somente estimativas identificadas como tal."))
    add("### Estimativas (preços de lista — não são custos faturados)\n")
    labels = {"bq_on_demand_analysis_month": "BigQuery on-demand (análise)", "bq_storage_month": "BigQuery armazenamento",
              "bq_reservation_baseline_month": "BigQuery reservas (slots baseline 24x7)", "gcs_storage_month": "Cloud Storage"}
    add(table(["Item", "Valor mensal estimado"], [[labels[k], money(est[k], est.get("currency"))] for k in labels if est.get(k) is not None]))
    if est.get("bq_on_demand_tib_month") is not None:
        add(f"Volume on-demand faturável normalizado: {n(est['bq_on_demand_tib_month'], 2)} TiB/mês.\n")
    add(f"_{est.get('label')}. Fonte dos preços: {est.get('source')}. On-demand considera apenas jobs sem reserva (evita dupla contagem com slots reservados)._\n")
    m = cost["metrics"]
    add(table(["Métrica de consumo (sem conversão monetária)", "Valor"], [["Bytes faturados na janela", b(m.get("bytes_billed_window"))],
                                                                           ["Bytes on-demand na janela", b(m.get("on_demand_bytes_billed_window"))],
                                                                           ["Slot-hours na janela", n(m.get("slot_hours_window"), 1)],
                                                                           ["Armazenamento BigQuery lógico / físico", f"{b(m.get('bq_logical_bytes'))} / {b(m.get('bq_physical_bytes'))}"],
                                                                           ["Cloud Storage", b(m.get("gcs_bytes"))]]))
    if cost.get("underutilization"):
        add("### Evidências de subutilização\n")
        add(table(["Recurso", "Evidência", "Confiança"], [[u["resource"], u["evidence"], u["confidence"]] for u in cost["underutilization"]]))

    # 9 ---------------------------------------------------------------
    add("## 9. Security & Governance\n")
    ss = sec["summary"]
    add(table(["Indicador", "Valor"], [
        ["Achados por severidade", ", ".join(f"{k}: {v}" for k, v in (ss.get("by_severity") or {}).items()) or NA],
        ["Achados relevantes para LGPD", n(ss.get("lgpd_relevant"))],
        ["Colunas com nomes indicativos de dados pessoais/saúde (não verificado)", f"{n(ss.get('sensitive_name_columns'))} em {n(ss.get('sensitive_name_tables'))} tabelas"],
        ["Colunas com policy tags / taxonomias", NB if blind(cov, "security", "policy_tags") else f"{n(ss.get('policy_tag_columns'))} / {n(ss.get('taxonomies'))}"],
        ["Tabelas com row-level security / políticas de masking", f"{n(ss.get('tables_with_row_access_policies'))} / {n(ss.get('data_policies'))}"],
        ["Perfis Sensitive Data Protection (alta sensibilidade)", NB if blind(cov, "security", "dlp_profiles") else f"{n(ss.get('dlp_profiles'))} ({n(ss.get('dlp_high'))})"],
        ["Datasets avaliados (CMEK)", f"{n(ss.get('datasets_evaluated'))} ({n(ss.get('datasets_cmek'))})"],
        ["Buckets (CMEK)", f"{n(ss.get('buckets'))} ({n(ss.get('buckets_cmek'))})"],
        ["Perímetros VPC-SC", NB if blind(cov, "security", "vpc_service_controls") else n(ss.get("vpc_sc_perimeters"))],
        ["Chaves user-managed de SA", NB if blind(cov, "security", "service_account_keys") else n(ss.get("sa_keys"))],
        ["Domínios corporativos considerados", ", ".join(ss.get("allowed_domains") or []) + (" (inferido)" if ss.get("allowed_domains_inferred") else "")]]))
    add(table(["Severidade", "Categoria", "Recurso", "Achado", "Evidência", "Recomendação", "LGPD"],
              [[f["severity"], f["category"], f["resource"], f["finding"], f["evidence"], f["recommendation"], "sim" if f["lgpd_relevant"] else ""]
               for f in sec["findings"][:40]], empty="_Nenhum achado (ou módulo de segurança não executado)._"))
    if sec.get("sensitive_columns_by_pattern"):
        add("Padrões de nome mais frequentes: " + ", ".join(f"`{k}` ({v})" for k, v in sec["sensitive_columns_by_pattern"].items()) + ".\n")

    # 10 --------------------------------------------------------------
    add("## 10. Migration Readiness\n")
    add(f"**Score: {n(rd.get('score'), 1)}/100 — {rd.get('level') or NA}** · cobertura de evidências: {pct(rd.get('evidence_coverage_pct'))} do peso total. "
        f"{rd['methodology']}\n")
    add(table(["Dimensão", "Peso", "Peso efetivo", "Score", "Evidência", "Regra"],
              [[k, n(d["weight"]), n(d["effective_weight"], 3), n(d["score"]) if d["score"] is not None else "não avaliado", d.get("evidence") or NA, d["rule"]]
               for k, d in rd["dimensions"].items()]))
    add("### Componentes e estratégias (hipóteses arquiteturais)\n")
    add(table(["Componente", "Destino (hipótese)", "Complexidade", "Lift-and-shift", "Modernização", "Riscos", "Coexistência", "Onda"],
              [[c["component"], c["target_hypothesis"], c["complexity"], c["lift_and_shift"], c["modernization"], c["risks"], c["coexistence"], c["wave"]]
               for c in rd["components"]]))
    regs = rd.get("regions") or {}
    add("### Disponibilidade do Databricks on GCP nas regiões do cliente\n")
    if regs.get("available"):
        add(table(["Localização GCP", "Região Databricks", "Suportada", "Recursos indisponíveis", "Somente com cross-geo"],
                  [[r["gcp_location"], f"{r['databricks_region']} ({r.get('location') or '?'})", "sim" if r["supported"] else "**não**",
                    ", ".join(r["missing_features"]) or "nenhum", ", ".join(r["cross_geo_only"]) or NA] for r in regs["regions"]]))
        add(f"_Snapshot da documentação em {regs.get('snapshot_date')} ({', '.join(regs.get('sources') or [])}). Revalidar antes de decisões._\n")
    else:
        add(f"_{regs.get('note')}_\n")
    add("### Ondas de migração sugeridas\n")
    add(table(["Onda", "Escopo"], [[w["wave"], w["scope"]] for w in rd["waves"]]))

    # 11 --------------------------------------------------------------
    add("## 11. Sizing Inputs\n")
    add(callout("IMPORTANT", "Slots do BigQuery **não** são convertidos em DBUs. Os números abaixo são insumos observados; o consumo em Databricks deve ser calibrado pelos benchmarks da POC."))
    si = sz["inputs"]
    add(table(["Insumo", "Valor"], [
        ["Volume inicial (lógico / físico)", f"{b(si['initial_migration_volume']['logical']['bytes'])} / {b(si['initial_migration_volume']['physical']['bytes'])}"],
        ["Armazenamento ativo (lógico)", b(si["active_storage"]["logical"]["bytes"])],
        ["Cloud Storage", NB if blind(cov, "cloud_storage", "volume_metrics") else b((si.get("gcs_volume") or {}).get("bytes"))],
        ["Ingestão diária (streaming / crescimento líquido)", f"{b((si['daily_ingestion'] or {}).get('streaming_bytes_per_day'))} / {b((si['daily_ingestion'] or {}).get('net_growth_bytes_per_day'))}"],
        ["Crescimento mensal", f"{b(si['monthly_growth_bytes'])} ({si['monthly_growth_source']})"],
        ["Pipelines (total / batch / streaming / execuções por dia)", f"{n(si['pipelines']['total'])} / {n(si['pipelines']['batch'])} / {n(si['pipelines']['streaming'])} / {n(si['pipelines']['runs_per_day'], 1)}"],
        ["Consultas por dia / jobs por dia", f"{n(si['queries']['queries_per_day'], 1)} / {n(si['queries']['jobs_per_day'], 1)}"],
        ["Hora de pico", f"{si['queries']['peak_hour_local']}h" if si["queries"]["peak_hour_local"] is not None else NA],
        ["Usuários (BigQuery humanos / viewers Power BI)", f"{n(si['users']['bigquery_human_principals'])} / {n(si['users']['powerbi_active_viewers'])}"],
        ["Concorrência (pico / média em minutos ativos)", f"{n(si['concurrency']['peak_concurrent_jobs'])} / {n(si['concurrency']['avg_concurrent_jobs_active_minutes'], 1)}"],
        ["Fator virada de mês / fim de semana", f"{n(si['peaks']['month_edge_factor'], 2)} / {n(si['peaks']['weekend_factor'], 2)}"],
        ["Latência observada (P50 / P95 / P99)", f"{ms(si['latency_observed']['p50_ms'])} / {ms(si['latency_observed']['p95_ms'])} / {ms(si['latency_observed']['p99_ms'])}"],
        ["Latência requerida", si["latency_observed"]["required"]],
        ["Relatórios / semantic models", f"{n(si['bi']['reports'])} / {n(si['bi']['semantic_models'])}"],
        ["Workloads ML/IA", f"Vertex em uso: {'sim' if si['ml_ai']['vertex_in_use'] else 'não'}; jobs BQML: {n(si['ml_ai']['bqml_statements'])}"],
        ["Disponibilidade", si["availability"]["requirement"]],
        ["Referência BigQuery (não converter)", f"{n(si['bigquery_reference_only']['slot_hours_window'], 1)} slot-hours / {b(si['bigquery_reference_only']['bytes_billed_window'])} em {si['bigquery_reference_only']['window_days']} dias"]]))
    add(table(["Classe de workload", "Slot-hours", "% slots", "Jobs"], [[k, n(v["slot_hours"], 1), pct(v["slot_pct"]), n(v["jobs"])] for k, v in si["workloads_by_class"].items()]))
    add("### Cenários\n")
    add(table(["Cenário", "Armazenamento lógico projetado", "Consultas/dia", "Pico de jobs concorrentes (limite superior)",
               "Execuções de pipeline/dia", "Premissas"],
              [[s["scenario"], b(s["storage_logical_bytes_12m"]), n(s["queries_per_day"], 1), n(s["peak_concurrent_queries"], 1),
                n(s["pipelines_runs_per_day"], 1), s["assumptions"]] for s in sz["scenarios"]]))
    add("_O número de clusters/warehouses não é estimado: depende do benchmark de concorrência na POC._\n")
    add("### Benchmarks necessários na POC\n")
    add(table(["Benchmark", "Objetivo", "Insumos"], [[x["benchmark"], x["goal"], ", ".join(str(i) for i in x["inputs"][:5]) or NA] for x in sz["poc_benchmarks"]]))

    # 12 --------------------------------------------------------------
    add("## 12. Risks & Recommendations\n")
    add(table(["Prioridade", "Tipo", "Área", "Descrição", "Recomendação"], [[x["priority"], x["type"], x["area"], x["text"], x["action"]] for x in recs]))
    if a.get("focus"):
        f = a["focus"]
        add(f"### Caso de uso: {f['name']}\n")
        sh = f["share_of_platform"]
        add(table(["Métrica", "Valor"], [
            ["Sementes (não encontradas)", f"{len(f['seeds'])} ({', '.join(f['seeds_not_found']) or 'nenhuma'})"],
            ["Tabelas / views / rotinas nos mesmos datasets", f"{n(f['tables'])} / {n(f['views'])} / {n(f['routines_in_same_datasets'])}"],
            ["Pipelines / modelos / relatórios", f"{n(f['pipelines'])} / {n(f['semantic_models'])} / {n(f['reports'])}"],
            ["Volume lógico", b(f["logical_bytes"])], ["Execuções de pipeline/dia", n(f["pipeline_runs_per_day"], 2)],
            ["Soma das durações médias da cadeia", secs(f["chain_duration_s_sum"])],
            ["Caminho crítico", " → ".join(f["critical_path"]) or NA],
            ["Custo atribuível (on-demand estimado/mês)", money(f["attributable"]["estimated_on_demand_month"], est.get("currency"))],
            ["Complexidade de migração", f["migration_complexity"]],
            ["% armazenamento", f"{pct(sh['storage_pct'], 2)} — denominador: {sh['storage_denominator']}"],
            ["% processamento", f"{pct(sh['processing_pct'], 2)} — denominador: {sh['processing_denominator']}"],
            ["% objetos", f"{pct(sh['objects_pct'], 2)} — denominador: {sh['objects_denominator']}"],
            ["% pipelines", f"{pct(sh['pipelines_pct'], 2)} — denominador: {sh['pipelines_denominator']}"]]))
        add(f"_{f['attributable']['note']}_\n")

    # 13 --------------------------------------------------------------
    add("## 13. Assessment Coverage\n")
    add("Status: **Complete** (coletado), **Partial** (parte dos escopos falhou — números são limite inferior), **Permission Denied**, "
        "**Not Available** (API desabilitada/indisponível), **Not Applicable**, **Not Assessed**.\n")
    add(table(["Status", "Componentes"], [[k, n(v)] for k, v in sorted(statuses.items())]))
    add(table(["Módulo", "Componente", "Status", "Encontrados", "Avaliados", "Permissões ausentes", "APIs indisponíveis", "Histórico", "Fonte", "Confiança", "Visibilidade", "Coletado em"],
              [[c["module"], c["component"], c["status"], n(c.get("resources_found")), n(c.get("resources_assessed")),
                "; ".join(c.get("missing_permissions") or []) or NA, ", ".join(c.get("unavailable_apis") or []) or NA, c.get("history_window") or NA,
                c.get("source") or NA, c.get("confidence"), c.get("visibility_note") or "", (c.get("collected_at") or "")[:19]] for c in cov]))
    lims = [(c["module"], c["component"], l) for c in cov for l in c.get("limitations") or []]
    if lims:
        add("<details><summary>Limitações registradas (" + str(len(lims)) + ")</summary>\n\n" +
            table(["Módulo", "Componente", "Limitação"], [list(x) for x in lims[:200]]) + "\n</details>\n")

    # Apêndices --------------------------------------------------------
    add("## 14. Apêndices\n")
    add("### A.1 Critérios de complexidade SQL\n" + CRITERIA_MD + "\n")
    add("### A.2 Migration Readiness Score\nCada dimensão gera 0–100 pela regra exibida na tabela do item 10. O score final é a média ponderada "
        "**somente** das dimensões com evidência; os pesos das demais são redistribuídos e a cobertura de evidências é reportada. Pesos configuráveis em `readiness_weights`.\n")
    add("### A.3 Classificação de workloads (precedência)\n"
        "1. Machine Learning — statements CREATE_MODEL/EXPORT_MODEL (alta)\n2. Data Science — principal casa `workload_rules.ds_principals` (média)\n"
        "3. Business Intelligence — principal casa `bi_principals` ou label de BI (média/alta)\n4. Data Engineering e ETL — labels de orquestração (alta), jobs LOAD ou principal de pipeline (média)\n"
        "5. Data Warehousing — ≥30% de statements de escrita SQL (média)\n6. Aplicações operacionais — SA com >500 jobs/dia ativos e <100 MB/job (baixa)\n"
        "7. BI (padrão) — SA com consultas recorrentes distribuídas em ≥8 horas do dia (baixa)\n8. Ad hoc Analytics — usuário humano (média)\n9. Outros — evidência insuficiente\n")
    add("### A.4 Regras de contabilização\n- Volume: BASE TABLE + MATERIALIZED VIEW; snapshots/clones apenas bytes físicos próprios; views/externas sem armazenamento BigQuery.\n"
        "- Registros: `TABLE_STORAGE.total_rows` (metadado), nunca COUNT(*).\n- Jobs: scripts multi-statement contam 1×; bytes/slots excluem o job pai `SCRIPT`.\n"
        "- Custos: faturado (Billing Export) ≠ estimado (preços de lista) ≠ métricas; on-demand só para jobs sem reserva.\n"
        "- Lineage: confirmed / inferred / unknown conforme a fonte de evidência.\n")
    add("### A.5 Arquivos gerados\n" + "\n".join(f"- `{f}`" for f in meta.get("files") or []) + "\n")
    return "\n".join(L)
