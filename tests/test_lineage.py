from collectors.lineage import Graph, to_graphml, to_mermaid


def g():
    x = Graph()
    for n in "abcde":
        x.node(n, "table", "bigquery", label=n)
    x.node("r", "report", "consumption", label="r")
    x.node("r2", "report", "consumption", label="r2")
    x.edge("a", "b", "t", "confirmed", "jobs")
    x.edge("b", "c", "t", "confirmed", "jobs")
    x.edge("c", "r", "t", "inferred", "pbi")
    x.edge("c", "r2", "t", "confirmed", "pbi")
    x.edge("d", "e", "t", "confirmed", "x")
    x.edge("e", "d", "t", "confirmed", "x")
    return x


def test_cycles_depth_critical_path():
    x = g()
    assert x.sccs() == [["d", "e"]]
    d = x.depths()
    assert d["r"] == 3 and d["a"] == 0
    assert x.critical_path()[0] == "a" and x.critical_path()[-1] in ("r", "r2")
    assert set(x.reachable("a", "down")) == {"b", "c", "r", "r2"}
    assert set(x.reachable("r", "up")) == {"a", "b", "c"}


def test_edge_merge_keeps_strongest_status():
    x = Graph()
    x.edge("a", "b", "t", "inferred", "h1", jobs=2)
    x.edge("a", "b", "t", "confirmed", "h2", jobs=3)
    e = x.edges[("a", "b", "t")]
    assert e["status"] == "confirmed" and e["jobs"] == 5 and e["evidence"] == ["h1", "h2"]


def test_exports():
    x = g()
    gm = to_graphml(x)
    assert gm.startswith("<?xml") and gm.count("<edge ") == len(x.edges)
    mm = to_mermaid(x)
    assert mm.startswith("flowchart LR") and "-.->" in mm
