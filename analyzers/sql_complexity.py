"""Complexidade de SQL e anti-patterns (item 12).

Três modos (config `sql.mode`):
  - none     : nenhuma definição SQL é lida.
  - features : as métricas abaixo são calculadas DENTRO do BigQuery via
               REGEXP_EXTRACT_ALL sobre view_definition / routine_definition /
               query; somente números saem do BigQuery (padrão).
  - full     : o texto SQL é lido (exige --include-sql) e analisado com SQLGlot
               (dialeto BigQuery), permitindo detecção de subqueries
               correlacionadas, joins não-equi, tabelas referenciadas etc.

Os mesmos padrões são usados nos dois lados, garantindo comparabilidade.

Critério de classificação (documentado no relatório):
  score = Σ peso(feature) × ocorrências + chars/2000
  Baixa < 15 ≤ Média < 45 ≤ Alta < 100 ≤ Muito alta
  SQL dinâmico, JavaScript UDF, remote function ou BigQuery ML elevam o objeto
  para no mínimo "Alta" (exigem refatoração manual).
"""
from __future__ import annotations

import re
from typing import Any

# feature -> (regex aplicada sobre o texto em MAIÚSCULAS, sem comentários, peso)
FEATURES: dict[str, tuple[str, float]] = {
    "joins": (r"\bJOIN\b", 2),
    "cross_joins": (r"\bCROSS\s+JOIN\b", 4),
    "or_in_join": (r"\bON\b[^;]{0,200}?\bOR\b", 3),
    "subqueries": (r"\(\s*SELECT\b", 2),
    "ctes": (r"(?:\bWITH\b|,)\s*`?[A-Z0-9_]+`?\s+AS\s*\(", 1),
    "select_distinct": (r"\bSELECT\s+DISTINCT\b", 1),
    "select_star": (r"\bSELECT\s+(?:[A-Z0-9_]+\.)?\*", 1),
    "window_functions": (r"\bOVER\s*\(", 2),
    "unnest": (r"\bUNNEST\s*\(", 2),
    "arrays_structs": (r"\b(?:ARRAY|STRUCT)\s*[<(]", 1),
    "qualify": (r"\bQUALIFY\b", 1),
    "merge": (r"\bMERGE\b", 3),
    "dynamic_sql": (r"\bEXECUTE\s+IMMEDIATE\b", 8),
    "procedural": (r"\b(?:DECLARE|LOOP|WHILE|REPEAT|BEGIN\s+TRANSACTION|EXCEPTION\s+WHEN)\b", 3),
    "js_udf": (r"\bLANGUAGE\s+JS\b", 8),
    "remote_function": (r"\bREMOTE\s+WITH\s+CONNECTION\b", 8),
    "bq_ml": (r"\bML\.[A-Z_]+\b", 6),
    "wildcard_tables": (r"_TABLE_SUFFIX|\*`", 3),
    "partition_pseudo_columns": (r"\b_PARTITION(?:TIME|DATE)\b", 1),
    "casts": (r"\b(?:SAFE_)?CAST\s*\(", 0.5),
    "bq_specific_functions": (r"\b(?:SAFE_CAST|SAFE\.|FARM_FINGERPRINT|GENERATE_(?:DATE_|TIMESTAMP_)?ARRAY|PARSE_(?:DATE|DATETIME|TIMESTAMP)|"
                              r"FORMAT_(?:DATE|DATETIME|TIMESTAMP)|REGEXP_CONTAINS|JSON_EXTRACT(?:_SCALAR|_ARRAY)?|"
                              r"ST_[A-Z]+|APPROX_[A-Z_]+|NET\.[A-Z_]+|KEYS\.[A-Z_]+|AEAD\.)", 1),
    "unions": (r"\bUNION\s+(?:ALL|DISTINCT)\b", 1),
    "group_by": (r"\bGROUP\s+BY\b", 0.5),
    "case_when": (r"\bCASE\b", 0.25),
}
FEATURE_NAMES = list(FEATURES)
REFACTOR_FEATURES = ("dynamic_sql", "js_udf", "remote_function", "bq_ml")
CLASS_THRESHOLDS = [(15, "Baixa"), (45, "Média"), (100, "Alta")]
CLASS_ORDER = ["Baixa", "Média", "Alta", "Muito alta"]

_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)


# ------------------------------------------------------ server-side (BigQuery)
def server_feature_select(column: str, prefix: str = "f_") -> str:
    """Expressões SQL que calculam as features dentro do BigQuery (o texto não é retornado)."""
    clean = (f"UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL({column}, ''), r'--[^\\n]*', ' '), "
             r"r'(?s)/\*.*?\*/', ' '))")
    parts = [f"LENGTH(IFNULL({column}, '')) AS {prefix}chars"]
    for name, (pattern, _) in FEATURES.items():
        bq_pat = pattern.replace("'", "\\'")
        parts.append(f"ARRAY_LENGTH(REGEXP_EXTRACT_ALL({clean}, r'{bq_pat}')) AS {prefix}{name}")
    return ",\n  ".join(parts)


def features_from_row(row: dict, prefix: str = "f_") -> dict:
    out = {"chars": int(row.get(f"{prefix}chars") or 0)}
    for name in FEATURE_NAMES:
        out[name] = int(row.get(f"{prefix}{name}") or 0)
    return out


# --------------------------------------------------------- client-side (texto)
def regex_features(sql: str) -> dict:
    text = _COMMENTS.sub(" ", sql or "").upper()
    out = {"chars": len(sql or "")}
    for name, (pattern, _) in FEATURES.items():
        out[name] = len(re.findall(pattern, text))
    return out


def sqlglot_analysis(sql: str) -> dict[str, Any]:
    """Análise AST com SQLGlot (se instalado). Retorna {} se indisponível ou se o parse falhar."""
    try:
        import sqlglot
        from sqlglot import exp
        from sqlglot.optimizer.scope import traverse_scope
    except ImportError:
        return {}
    try:
        statements = sqlglot.parse(sql, read="bigquery")
    except Exception as exc:  # parse error: SQL procedural, sintaxe não suportada etc.
        return {"parse_error": str(exc)[:200]}
    tables: set[str] = set()
    correlated = non_equi = or_joins = cross = 0
    for st in statements:
        if st is None:
            continue
        cte_names = {c.alias_or_name.lower() for c in st.find_all(exp.CTE)}
        for t in st.find_all(exp.Table):
            name = ".".join(p for p in [t.catalog, t.db, t.name] if p)
            if name and name.lower() not in cte_names:
                tables.add(name)
        for j in st.find_all(exp.Join):
            on = j.args.get("on")
            if (j.args.get("kind") or "").upper() == "CROSS" or (on is None and not j.args.get("using")):
                cross += 1
            if on is not None:
                if on.find(exp.Or):
                    or_joins += 1
                if not on.find(exp.EQ):
                    non_equi += 1
        try:
            for scope in traverse_scope(st):
                if getattr(scope, "is_correlated_subquery", False):
                    correlated += 1
        except Exception:
            pass
    return {"referenced_tables": sorted(tables), "correlated_subqueries": correlated, "non_equi_joins": non_equi,
            "or_joins_ast": or_joins, "cross_joins_ast": cross, "statements": len(statements)}


# --------------------------------------------------------------- scoring
def score(features: dict) -> float:
    s = sum(FEATURES[n][1] * (features.get(n) or 0) for n in FEATURE_NAMES)
    s += (features.get("chars") or 0) / 2000
    s += 4 * (features.get("correlated_subqueries") or 0) + 3 * (features.get("non_equi_joins") or 0)
    return round(s, 1)


def classify(features: dict) -> tuple[str, float, list[str]]:
    sc = score(features)
    cls = "Muito alta"
    for limit, name in CLASS_THRESHOLDS:
        if sc < limit:
            cls = name
            break
    reasons = [n for n in REFACTOR_FEATURES if features.get(n)]
    if reasons and CLASS_ORDER.index(cls) < CLASS_ORDER.index("Alta"):
        cls = "Alta"
    return cls, sc, reasons


def static_anti_patterns(features: dict) -> list[str]:
    ap = []
    if features.get("select_star"):
        ap.append("SELECT *")
    if (features.get("select_distinct") or 0) >= 2:
        ap.append("SELECT DISTINCT excessivo")
    if features.get("cross_joins") or features.get("cross_joins_ast"):
        ap.append("CROSS JOIN")
    if features.get("or_in_join") or features.get("or_joins_ast"):
        ap.append("JOIN com condição OR")
    if features.get("non_equi_joins"):
        ap.append("JOIN não-equi (risco de exploding join)")
    if features.get("correlated_subqueries"):
        ap.append("Subquery correlacionada")
    if (features.get("ctes") or 0) >= 8:
        ap.append("CTEs com muitos níveis")
    if (features.get("joins") or 0) >= 10:
        ap.append("JOINs complexos (>=10)")
    if (features.get("casts") or 0) >= 15:
        ap.append("Conversões de tipo recorrentes")
    if features.get("dynamic_sql"):
        ap.append("SQL dinâmico (EXECUTE IMMEDIATE)")
    if features.get("js_udf"):
        ap.append("JavaScript UDF")
    if features.get("remote_function"):
        ap.append("Remote function")
    if features.get("wildcard_tables"):
        ap.append("Wildcard tables (_TABLE_SUFFIX)")
    if features.get("bq_ml"):
        ap.append("BigQuery ML")
    return ap


def analyze_text(sql: str) -> dict:
    feats = regex_features(sql)
    ast = sqlglot_analysis(sql)
    feats.update({k: v for k, v in ast.items() if isinstance(v, int)})
    cls, sc, reasons = classify(feats)
    return {"features": feats, "complexity": cls, "score": sc, "refactor_reasons": reasons,
            "anti_patterns": static_anti_patterns(feats), "referenced_tables": ast.get("referenced_tables", []),
            "parser": "sqlglot" if "statements" in ast else "regex", "parse_error": ast.get("parse_error")}


def analyze_features(features: dict) -> dict:
    cls, sc, reasons = classify(features)
    return {"features": features, "complexity": cls, "score": sc, "refactor_reasons": reasons,
            "anti_patterns": static_anti_patterns(features), "referenced_tables": [], "parser": "bigquery-features"}


CRITERIA_MD = """
| Classe | Score | Regra adicional |
|---|---|---|
| Baixa | < 15 | — |
| Média | 15 – 44,9 | — |
| Alta | 45 – 99,9 | ou presença de SQL dinâmico, JavaScript UDF, remote function ou BigQuery ML |
| Muito alta | ≥ 100 | — |

Score = Σ (peso × ocorrências) + caracteres/2000 (+4 por subquery correlacionada e +3 por join não-equi no modo `full`).
Pesos: """ + ", ".join(f"{k}={v[1]:g}" for k, v in FEATURES.items()) + "."
