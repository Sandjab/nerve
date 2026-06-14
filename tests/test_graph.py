from nerve.graph import build_graph

def _row(s_key, s_name, pred, o_key, o_name, fid, s_m=1, o_m=1):
    return {"s_key": s_key, "s_name": s_name, "predicate": pred,
            "o_key": o_key, "o_name": o_name, "fact_id": fid,
            "s_mentions": s_m, "o_mentions": o_m, "confidence": 80,
            "document_id": 1}

def test_build_graph_collapses_nodes_and_dedups_links():
    rows = [
        _row("cluny", "Cluny", "fonde", "abbaye", "Abbaye", 1),
        _row("cluny", "cluny", "fonde", "abbaye", "Abbaye", 2),   # même triple -> 1 lien
        _row("cluny", "Cluny", "situe", "bourgogne", "Bourgogne", 3),
    ]
    g = build_graph(rows)
    assert sorted(n["id"] for n in g["nodes"]) == ["abbaye", "bourgogne", "cluny"]
    triples = sorted((l["source"], l["predicate"], l["target"]) for l in g["links"])
    assert triples == [("cluny", "fonde", "abbaye"), ("cluny", "situe", "bourgogne")]

def test_build_graph_label_is_most_mentioned():
    rows = [
        _row("cluny", "cluny", "p", "x", "X", 1, s_m=2),
        _row("cluny", "Cluny", "q", "y", "Y", 2, s_m=9),   # 9 > 2 -> libellé "Cluny"
    ]
    node = {n["id"]: n for n in build_graph(rows)["nodes"]}["cluny"]
    assert node["label"] == "Cluny"

def test_build_graph_skips_rows_without_key():
    rows = [_row(None, None, "p", "x", "X", 1), _row("a", "A", "p", "b", "B", 2)]
    g = build_graph(rows)
    assert sorted(n["id"] for n in g["nodes"]) == ["a", "b"]
    assert len(g["links"]) == 1
