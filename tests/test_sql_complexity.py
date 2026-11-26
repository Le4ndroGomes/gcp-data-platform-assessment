from analyzers.sql_complexity import FEATURE_NAMES, analyze_text, classify, regex_features, server_feature_select


def test_simple_query_is_low():
    a = analyze_text("SELECT id, nome FROM `p.d.t` WHERE dt >= '2024-01-01'")
    assert a["complexity"] == "Baixa"
    assert "p.d.t" in a["referenced_tables"] or a["parser"] == "regex"


def test_complex_query_flags_anti_patterns():
    sql = """
    WITH a AS (SELECT * FROM `p.d.t1`), b AS (SELECT DISTINCT x FROM `p.d.t2`), c AS (SELECT DISTINCT y FROM a)
    SELECT a.*, ROW_NUMBER() OVER (PARTITION BY a.k ORDER BY a.ts) rn
    FROM a JOIN b ON a.x = b.x OR a.y = b.x CROSS JOIN c, UNNEST(a.arr) e
    WHERE a.v > (SELECT AVG(v) FROM `p.d.t1` t WHERE t.k = a.k)
    QUALIFY rn = 1"""
    a = analyze_text(sql)
    aps = set(a["anti_patterns"])
    assert {"SELECT *", "CROSS JOIN", "JOIN com condição OR", "SELECT DISTINCT excessivo"} <= aps
    f = a["features"]
    assert f["qualify"] == 1 and f["unnest"] == 1 and f["window_functions"] == 1 and f["ctes"] >= 3


def test_refactor_features_force_high():
    assert classify({"js_udf": 1, "chars": 100})[0] == "Alta"
    assert classify({"dynamic_sql": 1})[0] == "Alta"


def test_comments_ignored():
    assert regex_features("-- SELECT * FROM x JOIN y\nSELECT 1")["joins"] == 0


def test_server_side_expression_covers_all_features():
    sql = server_feature_select("view_definition")
    for name in FEATURE_NAMES:
        assert f"AS f_{name}" in sql
    assert "view_definition" in sql and "REGEXP_EXTRACT_ALL" in sql
