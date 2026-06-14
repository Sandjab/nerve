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


def _krow(s_key, o_key, fid, set_id, s_kind="entity", o_kind="entity", conf=80):
    return {"s_key": s_key, "s_name": s_key.title(), "predicate": "p",
            "o_key": o_key, "o_name": o_key.title(), "fact_id": fid,
            "s_mentions": 1, "o_mentions": 1, "confidence": conf,
            "document_id": 1, "set_id": set_id, "s_kind": s_kind, "o_kind": o_kind}

def test_build_graph_kind_entity_dominates_value():
    rows = [_krow("x", "y", 1, 1, s_kind="value"),     # x vu d'abord en value
            _krow("x", "z", 2, 1, s_kind="entity")]    # puis en entity -> domine
    nodes = {n["id"]: n for n in build_graph(rows)["nodes"]}
    assert nodes["x"]["kind"] == "entity"
    assert nodes["y"]["kind"] == "entity"

def test_build_graph_node_only_value_stays_value():
    nodes = {n["id"]: n for n in build_graph([_krow("a", "9", 1, 1, o_kind="value")])["nodes"]}
    assert nodes["9"]["kind"] == "value"

def test_build_graph_sets_and_bridge():
    rows = [_krow("cluny", "abbaye", 1, 1),    # cluny dans set 1
            _krow("cluny", "odon", 2, 2)]      # ET set 2 -> hub multi-sets
    g = build_graph(rows)
    nodes = {n["id"]: n for n in g["nodes"]}
    assert nodes["cluny"]["sets"] == [1, 2]
    assert nodes["abbaye"]["sets"] == [1]
    links = {(l["source"], l["target"]): l for l in g["links"]}
    assert links[("cluny", "abbaye")]["is_bridge"] is True   # incident au hub
    assert links[("cluny", "odon")]["is_bridge"] is True

def test_build_graph_confidence_on_link_and_no_bridge_single_set():
    g = build_graph([_krow("a", "b", 7, 1, conf=55)])
    l = g["links"][0]
    assert l["confidence"] == 55 and l["is_bridge"] is False
