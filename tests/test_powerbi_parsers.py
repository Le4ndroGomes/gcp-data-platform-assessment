import json
import zipfile

from collectors.powerbi_parsers import dax_metrics, load_offline, model_from_tmdl, parameters_from_expressions, parse_m

M_BQ = '''let
    Source = GoogleBigQuery.Database([BillingProject="bill"]),
    #"proj-a" = Source{[Name="proj-a"]}[Data],
    ds = #"proj-a"{[Name="dw",Kind="Schema"]}[Data],
    t = ds{[Name="fato",Kind="Table"]}[Data],
    #"Mesclado" = Table.NestedJoin(t, {"k"}, Dim, {"k"}, "D", JoinKind.LeftOuter),
    #"Agrupado" = Table.Group(#"Mesclado", {"k"}, {{"n", each Table.RowCount(_)}})
in
    #"Agrupado"'''


def test_parse_m_bigquery_navigation():
    m = parse_m(M_BQ)
    assert m["bigquery_refs"] == ["proj-a.dw.fato"]
    assert m["billing_project"] == "bill"
    assert m["heavy_steps"] == {"Table.NestedJoin": 1, "Table.Group": 1}
    assert "bigquery" in m["source_types"]


def test_parse_m_parameter_resolution_and_native_sql():
    params = parameters_from_expressions([{"name": "Projeto", "expression": '"proj-x" meta [IsParameterQuery=true, Type="Text"]'}])
    expr = '''let
    Source = GoogleBigQuery.Database(),
    p = Source{[Name=Projeto]}[Data],
    q = Value.NativeQuery(p, "SELECT a FROM `proj-x.ds.t1` JOIN `proj-x.ds.t2` USING (k)", null, [EnableFolding=true])
in q'''
    m = parse_m(expr, params)
    assert params == {"Projeto": "proj-x"}
    assert len(m["native_queries"]) == 1
    refs = m["native_queries"][0]["referenced_tables"]
    assert not refs or {"proj-x.ds.t1", "proj-x.ds.t2"} <= set(refs)


def test_parse_m_other_sources():
    m = parse_m('let S = SharePoint.Files("https://x"), E = Excel.Workbook(S{0}[Content]) in E')
    assert set(m["source_types"]) == {"sharepoint", "excel"}


def test_dax_metrics():
    simple = dax_metrics("SUM(T[v])")
    heavy = dax_metrics("VAR a = CALCULATE(SUMX(FILTER(T, T[x] > 0), T[v] * T[q]), SAMEPERIODLASTYEAR(D[Data])) RETURN RANKX(ALL(U), a)")
    assert simple["complexity"] == "Baixa" and heavy["score"] > simple["score"]
    assert dax_metrics("[id] = USERPRINCIPALNAME()")["uses_userprincipalname"]


def test_tmdl_parser(tmp_path):
    d = tmp_path / "M.SemanticModel" / "definition" / "tables"
    d.mkdir(parents=True)
    tab = "\t"
    (d / "Vendas.tmdl").write_text(
        f"table Vendas\n{tab}measure 'Total' = SUM(Vendas[v])\n{tab}{tab}formatString: 0\n\n"
        f"{tab}measure Multi = ```\n{tab}{tab}{tab}VAR x = 1\n{tab}{tab}{tab}RETURN x\n{tab}{tab}{tab}```\n\n"
        f"{tab}column v\n{tab}{tab}dataType: double\n\n{tab}column dobro = [v] * 2\n{tab}{tab}dataType: double\n\n"
        f"{tab}partition Vendas = m\n{tab}{tab}mode: directQuery\n{tab}{tab}source =\n{tab}{tab}{tab}{tab}let\n"
        f"{tab}{tab}{tab}{tab}    S = GoogleBigQuery.Database()\n{tab}{tab}{tab}{tab}in\n{tab}{tab}{tab}{tab}    S\n", encoding="utf-8")
    (tmp_path / "M.SemanticModel" / "definition" / "relationships.tmdl").write_text(
        f"relationship r1\n{tab}fromColumn: Vendas.k\n{tab}toColumn: Dim.k\n{tab}crossFilteringBehavior: bothDirections\n", encoding="utf-8")
    model = model_from_tmdl(tmp_path / "M.SemanticModel" / "definition")
    t = model["tables"][0]
    assert [m["name"] for m in t["measures"]] == ["Total", "Multi"]
    assert "RETURN x" in t["measures"][1]["expression"]
    assert [c["type"] for c in t["columns"]] == ["data", "calculated"]
    assert t["partitions"][0]["mode"] == "directQuery" and "GoogleBigQuery" in t["partitions"][0]["source"]["expression"]
    assert model["relationships"][0]["crossFilteringBehavior"] == "bothDirections"


def test_load_offline_pbit_and_warnings(tmp_path):
    schema = {"model": {"tables": [{"name": "T", "columns": [], "measures": [{"name": "m", "expression": "1"}],
                                    "partitions": [{"mode": "import", "source": {"type": "m", "expression": "let a = 1 in a"}}]}]}}
    with zipfile.ZipFile(tmp_path / "rel.pbit", "w") as z:
        z.writestr("DataModelSchema", json.dumps(schema).encode("utf-16-le"))
        z.writestr("Report/Layout", json.dumps({"sections": [{"visualContainers": [1, 2]}]}).encode("utf-16-le"))
    (tmp_path / "x.pbix").write_bytes(b"binary")
    arts, warns = load_offline(tmp_path)
    assert arts[0]["kind"] == "pbit" and arts[0]["report"]["visuals"] == 2
    assert any(".pbix" in w for w in warns)
