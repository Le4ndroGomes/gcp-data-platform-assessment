"""Migration Readiness — Databricks (item 14).

Score 0–100 (maior = mais pronto / menor esforço-risco relativo), metodologia:
  - 6 dimensões com pesos configuráveis (`readiness_weights`);
  - cada dimensão só entra no cálculo se houver evidência coletada para ela;
    dimensões sem evidência são EXCLUÍDAS e os pesos renormalizados — nunca
    recebem pontuação arbitrária;
  - o relatório exibe o score, a cobertura de evidências (% do peso total que
    pôde ser avaliado) e as entradas/regras de cada dimensão.
As correspondências tecnológicas são hipóteses arquiteturais, não substituições automáticas.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.context import load_yaml

PLAYBOOK = {
    "bigquery": {"lift": "Exportar tabelas para Parquet/Delta (EXPORT DATA ou leitura via conector Spark BigQuery / Lakehouse Federation) e recriar no Unity Catalog",
                 "modern": "Medallion em Delta Lake com Liquid Clustering, SQL traduzido (SQLGlot/BladeBridge/Lakebridge), Databricks SQL serverless",
                 "risks": "Dialeto SQL (SAFE_*, ARRAY/STRUCT, QUALIFY já suportado, funções de data), semântica de particionamento e wildcard tables",
                 "coexist": "Lakehouse Federation para BigQuery durante a transição"},
    "gcs": {"lift": "External Locations + tabelas externas/Volumes no Unity Catalog sobre os mesmos buckets",
            "modern": "Conversão para Delta/Iceberg gerenciado; compactação de small files; Auto Loader para ingestão",
            "risks": "Small files, formatos heterogêneos, políticas de lifecycle", "coexist": "Leitura direta dos buckets existentes"},
    "biglake": {"lift": "Tabelas externas no Unity Catalog sobre os mesmos URIs", "modern": "Tabelas Delta gerenciadas ou Iceberg via UniForm",
                "risks": "Conexões e permissões de BigLake", "coexist": "Federation / leitura direta"},
    "dataform": {"lift": "Converter SQLX em notebooks/arquivos SQL orquestrados por Lakeflow Jobs", "modern": "Lakeflow Spark Declarative Pipelines (MVs/streaming tables) ou dbt",
                 "risks": "Macros JavaScript, assertions, incrementais", "coexist": "Executar em paralelo até validação de paridade"},
    "composer": {"lift": "Manter Airflow chamando Databricks (operadores Databricks) na fase inicial", "modern": "Lakeflow Jobs (tarefas, dependências, gatilhos por arquivo/tabela)",
                 "risks": "Operadores GCP-específicos, sensores, XCom", "coexist": "Airflow orquestrando jobs Databricks"},
    "dataflow": {"lift": "Reescrever pipelines Beam em Spark", "modern": "Structured Streaming / Spark Declarative Pipelines; Auto Loader",
                 "risks": "Semântica de janelas/watermarks, conectores Beam específicos", "coexist": "Dataflow gravando em GCS lido pelo Databricks"},
    "dataproc": {"lift": "Migrar jobs Spark com ajustes mínimos para Databricks Compute (Jobs)", "modern": "Serverless jobs, Photon, Delta",
                 "risks": "Dependências Hadoop/Hive, init actions", "coexist": "Leitura dos mesmos dados no GCS"},
    "datastream": {"lift": "Manter Datastream gravando em GCS e consumir com Auto Loader", "modern": "Lakeflow Connect (CDC gerenciado) onde disponível na região",
                   "risks": "Disponibilidade regional do Lakeflow Connect; ordenação/deduplicação CDC", "coexist": "Datastream -> GCS -> Delta (APPLY CHANGES)"},
    "pubsub": {"lift": "Conector Pub/Sub do Structured Streaming", "modern": "Streaming tables (Spark Declarative Pipelines); Zerobus Ingest onde disponível",
               "risks": "Exactly-once, ordering keys", "coexist": "Manter Pub/Sub como barramento"},
    "datafusion": {"lift": "Reimplementar pipelines CDAP", "modern": "Lakeflow Connect + Spark Declarative Pipelines",
                   "risks": "Plugins proprietários, transformações visuais", "coexist": "Data Fusion gravando no GCS/Delta"},
    "scheduler": {"lift": "Gatilhos agendados de Lakeflow Jobs", "modern": "Gatilhos por chegada de arquivo/atualização de tabela", "risks": "Alvos HTTP diversos", "coexist": "Scheduler chamando Jobs API"},
    "workflows": {"lift": "Lakeflow Jobs (tarefas condicionais/for-each)", "modern": "Lakeflow Jobs", "risks": "Conectores Workflows para APIs GCP", "coexist": "Workflows chamando Jobs API"},
    "functions": {"lift": "Tarefas Python em Lakeflow Jobs", "modern": "Jobs serverless / Databricks Apps", "risks": "Gatilhos orientados a eventos", "coexist": "Manter funções para eventos"},
    "cloudrun": {"lift": "Avaliar caso a caso", "modern": "Databricks Apps / Model Serving", "risks": "Serviços operacionais fora do escopo de dados", "coexist": "Manter"},
    "vertex": {"lift": "Registrar modelos no MLflow / Unity Catalog", "modern": "Mosaic AI (treino, Feature Engineering, Model Serving, Agent Bricks)",
               "risks": "Disponibilidade regional de Model Serving/FMAPI", "coexist": "Vertex consumindo Delta via conector"},
    "dataplex": {"lift": "Mapear policy tags, glossário e data quality para Unity Catalog", "modern": "Unity Catalog (tags, ABAC, column masks, row filters, lineage, Data Quality Monitoring)",
                 "risks": "Taxonomias e regras DQ", "coexist": "Catálogos em paralelo"},
    "looker": {"lift": "Apontar Looker para Databricks SQL", "modern": "AI/BI Dashboards e Genie para casos selecionados", "risks": "LookML específico de BigQuery", "coexist": "Looker em ambos"},
    "cloudsql": {"lift": "Fonte: Lakeflow Connect / Lakehouse Federation", "modern": "Lakebase para cargas OLTP associadas a dados analíticos", "risks": "CDC (binlog/logical decoding)", "coexist": "Permanece como fonte"},
    "alloydb": {"lift": "Fonte via Federation (PostgreSQL) / CDC", "modern": "Lakebase (avaliar)", "risks": "CDC", "coexist": "Permanece como fonte"},
    "spanner": {"lift": "Fonte via exportação/CDC (change streams)", "modern": "Avaliar", "risks": "Change streams", "coexist": "Permanece como fonte"},
    "compute": {"lift": "Avaliar finalidade das VMs (gateways PBI, ETL legado)", "modern": "Caso a caso", "risks": "Dependências ocultas", "coexist": "Manter"},
    "gke": {"lift": "Avaliar finalidade", "modern": "Caso a caso", "risks": "Workloads Spark-on-K8s", "coexist": "Manter"},
    "powerbi": {"lift": "Power BI conectado ao Databricks SQL (DirectQuery/Import) com semantic models reapontados",
                "modern": "Camada gold + metric views; AI/BI Dashboards e Genie para relatórios selecionados; Publish to Power BI",
                "risks": "Power Query com transformações pesadas, SQL nativo BigQuery, RLS dinâmico", "coexist": "Reapontar modelo a modelo"},
}


def _region_report(inv: dict, config: dict, base_dir: Path) -> dict:
    path = Path(config.get("databricks", {}).get("regions_snapshot_file") or "config/databricks_gcp_regions.yaml")
    if not path.is_absolute():
        path = base_dir / path
    if not path.exists():
        return {"available": False, "note": f"Snapshot de regiões não encontrado ({path})"}
    snap = load_yaml(path)
    regions = snap.get("regions") or {}
    used = {}
    for p in inv.get("projects") or []:
        for loc in p.get("bigquery_locations") or []:
            used[loc] = used.get(loc, 0) + 1
    out = []
    for loc in sorted(used):
        cands = snap.get("multi_region_candidates", {}).get(loc.upper()) if loc.upper() in ("US", "EU") else [loc.lower()]
        for c in cands or []:
            feats = regions.get(c)
            out.append({"gcp_location": loc, "databricks_region": c, "supported": feats is not None,
                        "missing_features": [f for f, v in (feats or {}).items() if v is False],
                        "cross_geo_only": [f for f, v in (feats or {}).items() if v == "cross_geo"],
                        "location": (feats or {}).get("location")})
    return {"available": True, "snapshot_date": snap.get("snapshot_date"), "sources": snap.get("sources"), "regions": out}


def _dim(score, coverage, inputs, rule):
    return {"score": None if score is None else max(0, min(100, round(score))), "evidence": coverage, "inputs": inputs, "rule": rule}


def analyze(inv: dict, config: dict, *, volume: dict, sql: dict, pipelines: dict, pbi: dict, security: dict,
            footprint: dict, coverage: list[dict], base_dir: Path) -> dict:
    weights = config.get("readiness_weights", {})
    cov_status = {(c["module"], c["component"]): c["status"] for c in coverage}

    def assessed(module, comp):
        return cov_status.get((module, comp)) in ("Complete", "Partial")

    dims = {}
    # 1. volume
    tb = (volume.get("logical") or {}).get("TB") or 0
    if volume.get("tables_counted") or assessed("bigquery", "volumetry"):
        s = 90 if tb < 10 else 75 if tb < 100 else 60 if tb < 500 else 45 if tb < 1000 else 30
        lt_share = ((volume.get("long_term_logical") or {}).get("bytes") or 0) / max((volume.get("logical") or {}).get("bytes") or 1, 1)
        s += 5 if lt_share > 0.6 else 0
        dims["data_volume"] = _dim(s, "BigQuery TABLE_STORAGE", {"logical_TB": tb, "long_term_share": round(lt_share, 2)},
                                   "<10TB=90; <100=75; <500=60; <1PB=45; senão 30; +5 se >60% long-term")
    else:
        dims["data_volume"] = _dim(None, None, {}, "sem evidência de volumetria")
    # 2. SQL
    objs = [o for o in sql.get("objects") or [] if o.get("complexity")]
    if objs:
        high = sum(1 for o in objs if o["complexity"] in ("Alta", "Muito alta")) / len(objs)
        med = sum(1 for o in objs if o["complexity"] == "Média") / len(objs)
        refactor = sum(1 for o in objs if o.get("refactor_reasons"))
        s = 100 - 60 * high - 25 * med - min(20, refactor * 0.5)
        dims["sql_complexity"] = _dim(s, f"{len(objs)} objetos SQL ({sql.get('sql_mode')})",
                                      {"objects": len(objs), "pct_high": round(100 * high, 1), "pct_medium": round(100 * med, 1),
                                       "requires_refactor": refactor},
                                      "100 − 60×%Alta/MuitoAlta − 25×%Média − min(20; 0,5×objetos que exigem refatoração)")
    else:
        dims["sql_complexity"] = _dim(None, None, {}, "sem definições/feature de SQL coletadas")
    # 3. pipelines
    pipes_assessed = any(assessed("pipelines", c) for c in ("bigquery_transfers", "composer", "dataflow", "dataform", "scheduler", "datastream"))
    if pipelines.get("total") or pipes_assessed:
        tot = max(pipelines.get("total") or 0, 1)
        hc = pipelines.get("by_complexity") or {}
        hi = (hc.get("Alta", 0) + hc.get("Muito alta", 0)) / tot
        stream = (pipelines.get("streaming") or 0) / tot
        ext = len(pipelines.get("external_dependencies") or [])
        s = 100 - 50 * hi - 20 * stream - min(15, ext * 1.5) - min(10, len(pipelines.get("unstable") or []))
        if not pipelines.get("total"):
            s = 95
        dims["pipeline_complexity"] = _dim(s, f"{pipelines.get('total', 0)} pipelines",
                                           {"pipelines": pipelines.get("total"), "pct_high": round(100 * hi, 1),
                                            "pct_streaming": round(100 * stream, 1), "external_dependencies": ext,
                                            "unstable": len(pipelines.get("unstable") or [])},
                                           "100 − 50×%Alta − 20×%streaming − min(15; 1,5×dependências externas) − min(10; instáveis)")
    else:
        dims["pipeline_complexity"] = _dim(None, None, {}, "pipelines não avaliados")
    # 4. BI
    if pbi.get("semantic_models"):
        n = pbi["semantic_models"]
        native = (pbi.get("native_sql_models") or 0) / n
        heavy = len(pbi.get("push_down_candidates") or []) / n
        dyn = (pbi.get("dynamic_rls_models") or 0) / n
        imp = sum(v for k, v in (pbi.get("storage_modes") or {}).items() if k and k.startswith("Import")) / n
        s = 100 - 30 * native - 30 * heavy - 15 * dyn - 10 * imp
        dims["bi_coupling"] = _dim(s, f"{n} semantic models ({pbi.get('mode')})",
                                   {"models": n, "pct_native_sql": round(100 * native, 1), "pct_heavy_power_query": round(100 * heavy, 1),
                                    "pct_dynamic_rls": round(100 * dyn, 1), "pct_import": round(100 * imp, 1)},
                                   "100 − 30×%SQL nativo − 30×%Power Query pesado − 15×%RLS dinâmico − 10×%Import")
    else:
        dims["bi_coupling"] = _dim(None, None, {}, "Power BI não avaliado")
    # 5. recursos específicos da plataforma
    if objs or volume.get("tables_counted"):
        feats = 0
        langs = sql.get("routine_languages") or {}
        feats += 3 * sum(v for k, v in langs.items() if k not in ("SQL", "", None))
        feats += 3 * (sql.get("remote_functions") or 0)
        st = sql.get("static_anti_patterns") or {}
        feats += 2 * st.get("BigQuery ML", 0) + st.get("Wildcard tables (_TABLE_SUFFIX)", 0) + 2 * st.get("SQL dinâmico (EXECUTE IMMEDIATE)", 0)
        active = {r["key"] for r in footprint.get("services") or [] if r["status"].startswith("Em uso")}
        feats += 5 * len(active & {"looker", "biglake", "datafusion", "vertex"})
        bi_engine = any(c.get("bi_engine") and c["bi_engine"].get("size_bytes") for c in inv.get("capacity") or [])
        feats += 5 if bi_engine else 0
        dims["platform_specific_features"] = _dim(100 - min(70, feats), "rotinas, SQL e serviços em uso",
                                                  {"weighted_feature_points": feats, "bi_engine": bi_engine,
                                                   "specific_services_in_use": sorted(active & {"looker", "biglake", "datafusion", "vertex"})},
                                                  "100 − min(70; 3×UDF não-SQL + 3×remote + 2×BQML + wildcard + 2×SQL dinâmico + 5×serviço específico + 5×BI Engine)")
    else:
        dims["platform_specific_features"] = _dim(None, None, {}, "sem evidência")
    # 6. segurança/governança
    sec_assessed = any(assessed("security", c) for c in ("iam_policies", "dataset_acl", "policy_tags"))
    if sec_assessed:
        sm = security.get("summary") or {}
        kinds = {}
        for f in security.get("findings") or []:
            kinds.setdefault(f["severity"], set()).add(re.sub(r"\d+", "N", f["finding"]))
        sev = {k: len(v) for k, v in kinds.items()}
        s = 100 - 8 * sev.get("Alta", 0) - 3 * sev.get("Média", 0)
        s -= 10 if sm.get("policy_tag_columns") else 0
        s -= 5 if sm.get("tables_with_row_access_policies") else 0
        s -= 10 if sm.get("sensitive_name_columns") else 0
        dims["security_governance"] = _dim(s, "IAM, ACLs, policy tags, RLS, nomes de colunas",
                                           {"distinct_high_finding_types": sev.get("Alta", 0), "distinct_medium_finding_types": sev.get("Média", 0),
                                            "policy_tag_columns": sm.get("policy_tag_columns"), "row_policies_tables": sm.get("tables_with_row_access_policies"),
                                            "sensitive_name_columns": sm.get("sensitive_name_columns")},
                                           "100 − 8×tipos de achado Alta − 3×tipos Média (achados repetidos por projeto contam 1×) − 10 se policy tags − 5 se RLS − 10 se colunas sensíveis (mapear para Unity Catalog)")
    else:
        dims["security_governance"] = _dim(None, None, {}, "segurança não avaliada")

    total_w = sum(weights.get(k, 0) for k in dims)
    used_w = sum(weights.get(k, 0) for k, d in dims.items() if d["score"] is not None)
    score = round(sum(weights.get(k, 0) * d["score"] for k, d in dims.items() if d["score"] is not None) / used_w, 1) if used_w else None
    for k, d in dims.items():
        d["weight"] = weights.get(k, 0)
        d["effective_weight"] = round(weights.get(k, 0) / used_w, 3) if used_w and d["score"] is not None else 0
    level = None if score is None else "Alta prontidão" if score >= 75 else "Prontidão moderada" if score >= 55 else "Baixa prontidão"

    components = []
    active_rows = [r for r in footprint.get("services") or [] if r["status"] in ("Em uso (evidência)", "Provisionado sem evidência de uso")]
    for r in active_rows:
        pb = PLAYBOOK.get(r["key"], {})
        components.append({"component": r["service"], "status": r["status"], "target_hypothesis": r["databricks_target"],
                           "complexity": _component_complexity(r["key"], sql, pipelines, volume), "lift_and_shift": pb.get("lift"),
                           "modernization": pb.get("modern"), "risks": pb.get("risks"), "coexistence": pb.get("coexist"),
                           "wave": _wave(r["key"])})
    if pbi.get("semantic_models"):
        pb = PLAYBOOK["powerbi"]
        components.append({"component": "Microsoft Power BI", "status": "Em uso (evidência)", "target_hypothesis": "Power BI + Databricks SQL; AI/BI/Genie seletivo",
                            "complexity": "Alta" if (dims["bi_coupling"]["score"] or 100) < 55 else "Média",
                            "lift_and_shift": pb["lift"], "modernization": pb["modern"], "risks": pb["risks"],
                            "coexistence": pb["coexist"], "wave": "Onda 1 (POC) e Onda 3"})
    return {"score": score, "level": level, "evidence_coverage_pct": round(100 * used_w / total_w, 1) if total_w else 0,
            "dimensions": dims, "weights": weights, "components": components,
            "regions": _region_report(inv, config, base_dir), "waves": WAVES,
            "methodology": "Média ponderada das dimensões com evidência; dimensões sem evidência excluídas e pesos renormalizados."}


def _component_complexity(key: str, sql: dict, pipelines: dict, volume: dict) -> str:
    if key == "bigquery":
        dist = {}
        for d in (sql.get("distribution") or {}).values():
            for k, v in d.items():
                dist[k] = dist.get(k, 0) + v
        tot = sum(dist.values()) or 1
        hi = (dist.get("Alta", 0) + dist.get("Muito alta", 0)) / tot
        tb = (volume.get("logical") or {}).get("TB") or 0
        return "Muito alta" if hi > 0.3 or tb > 1000 else "Alta" if hi > 0.15 or tb > 200 else "Média"
    return {"dataflow": "Alta", "datafusion": "Alta", "composer": "Média", "dataform": "Média", "datastream": "Média",
            "dataproc": "Média", "vertex": "Alta", "looker": "Média", "gcs": "Baixa", "biglake": "Baixa",
            "scheduler": "Baixa", "workflows": "Média", "pubsub": "Média", "functions": "Média"}.get(key, "A avaliar")


def _wave(key: str) -> str:
    return {"gcs": "Onda 0 (fundação)", "dataplex": "Onda 0 (fundação)", "bigquery": "Onda 1 (POC) → Onda 2",
            "biglake": "Onda 2", "dataform": "Onda 2", "scheduler": "Onda 3", "workflows": "Onda 3", "composer": "Onda 3",
            "dataproc": "Onda 3", "dataflow": "Onda 4", "datastream": "Onda 4", "pubsub": "Onda 4", "datafusion": "Onda 4",
            "vertex": "Onda 4", "looker": "Onda 3"}.get(key, "Avaliar")


WAVES = [
    {"wave": "Onda 0 — Fundação", "scope": "Workspace(s), Unity Catalog, rede privada, identidades, External Locations sobre GCS, governança (tags/masks)"},
    {"wave": "Onda 1 — POC / caso de uso", "scope": "Caso de uso selecionado (item 20): tabelas, pipelines e modelo Power BI em DirectQuery + AI/BI/Genie"},
    {"wave": "Onda 2 — Data Warehouse", "scope": "Datasets por ordem de dependência (lineage), começando pelos de menor complexidade; Federation para coexistência"},
    {"wave": "Onda 3 — Pipelines e BI", "scope": "Dataform/scheduled queries/Composer -> Lakeflow; reapontar semantic models"},
    {"wave": "Onda 4 — Streaming, CDC e ML", "scope": "Datastream/Pub/Sub/Dataflow -> Lakeflow Connect/Structured Streaming; Vertex -> Mosaic AI"},
    {"wave": "Onda 5 — Descomissionamento", "scope": "Validação de paridade, corte, desligamento e FinOps pós-migração"},
]
