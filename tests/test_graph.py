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


def _krow(s_key, o_key, fid, set_id, s_kind="concept", o_kind="concept",
         conf=80, s_eid=None, o_eid=None, s_votes=None, o_votes=None):
    import json
    return {"fact_id": fid, "predicate": "rel", "confidence": conf,
            "s_key": s_key, "s_name": s_key, "s_mentions": 1,
            "o_key": o_key, "o_name": o_key, "o_mentions": 1,
            "document_id": 1, "set_id": set_id, "s_kind": s_kind, "o_kind": o_kind,
            "s_entity_id": s_eid if s_eid is not None else hash(("s", s_key, set_id)),
            "o_entity_id": o_eid if o_eid is not None else hash(("o", o_key, set_id)),
            "s_votes": json.dumps(s_votes or {s_kind: 1}),
            "o_votes": json.dumps(o_votes or {o_kind: 1})}

def test_build_graph_kind_vote_majoritaire_au_collapse():
    # x apparaît dans deux entités-docs (eid distincts) : organisation (3 votes) vs lieu (1)
    rows = [_krow("x", "y", 1, 1, s_eid=10, s_votes={"organisation": 3}),
            _krow("x", "z", 2, 2, s_eid=20, s_votes={"lieu": 1})]
    nodes = {n["id"]: n for n in build_graph(rows)["nodes"]}
    assert nodes["x"]["kind"] == "organisation"

def test_build_graph_collapse_dedup_par_entity_id():
    # même entité-doc (eid 10) vue dans 2 faits : ses votes ne comptent qu'une fois
    rows = [_krow("x", "y", 1, 1, s_eid=10, s_votes={"lieu": 1}),
            _krow("x", "z", 2, 1, s_eid=10, s_votes={"lieu": 1}),
            _krow("x", "w", 3, 1, s_eid=99, s_votes={"organisation": 1})]
    nodes = {n["id"]: n for n in build_graph(rows)["nodes"]}
    assert nodes["x"]["kind"] == "lieu"            # 1 vote lieu vs 1 vote organisation -> tie-break ordre -> lieu
    assert "s_entity_id" not in nodes["x"] and "s_votes" not in nodes["x"]

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
