# nerve/graph.py
"""Assemblage pur d'un graphe {nodes, links} à partir de lignes de faits enrichies
(sujet/objet -> normalized_key + canonical_name). Aucune dépendance DB/réseau :
l'identité de nœud est la clé normalisée (collapse cross-document) ; les liens sont
dédupliqués par (s_key, predicate, o_key)."""


def _add_node(nodes: dict, key: str, name: str, mentions: int) -> None:
    n = nodes.get(key)
    if n is None:
        nodes[key] = {"id": key, "label": name or key, "mentions": mentions or 0}
    elif (mentions or 0) > n["mentions"]:
        n["label"] = name or key
        n["mentions"] = mentions or 0


def build_graph(rows: list[dict]) -> dict:
    nodes: dict = {}
    links: dict = {}
    for r in rows:
        s_key, o_key = r.get("s_key"), r.get("o_key")
        if not s_key or not o_key:
            continue
        _add_node(nodes, s_key, r.get("s_name"), r.get("s_mentions"))
        _add_node(nodes, o_key, r.get("o_name"), r.get("o_mentions"))
        lk = (s_key, r.get("predicate"), o_key)
        if lk not in links:
            links[lk] = {"source": s_key, "target": o_key,
                         "predicate": r.get("predicate"), "fact_id": r.get("fact_id")}
    return {"nodes": list(nodes.values()), "links": list(links.values())}
