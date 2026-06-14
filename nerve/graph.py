# nerve/graph.py
"""Assemblage pur d'un graphe {nodes, links} à partir de lignes de faits enrichies.
Identité de nœud = clé normalisée (collapse cross-document). Liens dédupliqués par
(s_key, predicate, o_key). Le nœud porte son type (kind, 'entity' domine 'value') et
la liste des sets où sa clé apparaît ; le lien porte la confiance et un drapeau
is_bridge (incident à un nœud hub multi-sets = passerelle transverse)."""


def _add_node(nodes: dict, key: str, name: str, mentions: int,
              kind: str, set_id) -> None:
    n = nodes.get(key)
    if n is None:
        n = {"id": key, "label": name or key, "mentions": mentions or 0,
             "kind": kind or "entity", "_sets": set()}
        nodes[key] = n
    elif (mentions or 0) > n["mentions"]:
        n["label"] = name or key
        n["mentions"] = mentions or 0
    if (kind or "entity") == "entity":          # entity domine value
        n["kind"] = "entity"
    if set_id is not None:
        n["_sets"].add(set_id)


def build_graph(rows: list[dict]) -> dict:
    nodes: dict = {}
    links: dict = {}
    for r in rows:
        s_key, o_key = r.get("s_key"), r.get("o_key")
        if not s_key or not o_key:
            continue
        _add_node(nodes, s_key, r.get("s_name"), r.get("s_mentions"),
                  r.get("s_kind"), r.get("set_id"))
        _add_node(nodes, o_key, r.get("o_name"), r.get("o_mentions"),
                  r.get("o_kind"), r.get("set_id"))
        lk = (s_key, r.get("predicate"), o_key)
        if lk not in links:
            links[lk] = {"source": s_key, "target": o_key,
                         "predicate": r.get("predicate"), "fact_id": r.get("fact_id"),
                         "confidence": r.get("confidence")}
    for n in nodes.values():
        n["sets"] = sorted(n.pop("_sets"))
    multi = {key for key, n in nodes.items() if len(n["sets"]) > 1}
    for lk in links.values():
        lk["is_bridge"] = lk["source"] in multi or lk["target"] in multi
    return {"nodes": list(nodes.values()), "links": list(links.values())}
