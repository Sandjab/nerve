# Plan 2 / I-4 · Modèle 3-niveaux (sets + transverse + recherche) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Donner accès aux 3 niveaux (Document/Set/Transverse) + recherche sémantique globale, en consommant `vec_facts` (recherche) et `vec_entities` (transverse), avec une UI minimale.

**Architecture:** Module pur `graph.py` (assemblage `{nodes,links}`, collapse cross-document par `normalized_key`) ; requêtes lecture seule dans `store.py` (sets, graphe de set, KNN `vec_facts`/`vec_entities` avec **over-fetch** pour le filtre `sets`) ; endpoints FastAPI `async` (sets, set-graph, search, transverse) ; UI minimale dans `web/index.html`.

**Tech Stack:** Python 3.11+, FastAPI, SQLite + `sqlite-vec` v0.1.9 (KNN `embedding MATCH ? AND k = ?`, distance L2 ≡ classement cosinus car embeddings L2-normalisés), force-graph (front), pytest (asyncio auto).

**Réf. spec :** `docs/superpowers/specs/2026-06-14-nerve-I4-3levels-design.md`.

---

## File Structure

- **Create** `nerve/graph.py` — `build_graph(rows) -> {nodes, links}` (pur, sans DB).
- **Create** `tests/test_graph.py`.
- **Modify** `nerve/store.py` — `list_sets`/`get_set`/`facts_for_set`/`search_facts`/`entities_by_key`/`entity_neighbors`/`facts_for_entities` (lecture seule ; `import sqlite_vec` déjà présent).
- **Modify** `nerve/api.py` — `Query` ; imports `build_graph`/`normalized_key`/`embed` ; routes `GET /api/sets`, `/api/sets/{id}`, `/api/sets/{id}/graph`, `/api/search`, `/api/transverse`.
- **Modify** `nerve/web/index.html` — UI minimale (sidebar sets/docs, recherche, transverse) ; `nodeLabel` utilise `label||id`.
- **Modify** `tests/test_store.py`, `tests/test_api.py`.

Conventions reprises : tests sans réseau (`monkeypatch` sur `embed`), `asyncio_mode=auto`, `TestClient` + `importlib.reload(api)` après `setenv`. **Faits sans `*_entity_id` → exclus du graphe** (INNER JOIN entities ; décision spec §4/§11). Vec tables : `add_fact_vector` n'insère que les faits **non-dup** (déjà le cas).

---

## Task 1 : store.py — list_sets / get_set

**Files:** Modify `nerve/store.py` ; Test `tests/test_store.py`

- [ ] **Step 1 : Tests rouges** — ajouter à `tests/test_store.py` :

```python
def test_list_sets_counts_documents(tmp_path):
    st = Store(str(tmp_path / "ls.db"), embed_dim=3); st.init_db()
    s1 = st.create_set("Alpha"); s2 = st.create_set("Beta")
    st.create_document(s1, "d1", "text"); st.create_document(s1, "d2", "text")
    rows = st.list_sets()
    by_id = {r["id"]: r for r in rows}
    assert by_id[s1]["name"] == "Alpha" and by_id[s1]["document_count"] == 2
    assert by_id[s2]["document_count"] == 0

def test_get_set_with_documents(tmp_path):
    st = Store(str(tmp_path / "gs.db"), embed_dim=3); st.init_db()
    s = st.create_set("S")
    d = st.create_document(s, "doc", "text")
    out = st.get_set(s)
    assert out["name"] == "S"
    assert [doc["id"] for doc in out["documents"]] == [d]
    assert st.get_set(9999) is None
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_store.py -q` → FAIL (`list_sets` absent).

- [ ] **Step 3 : Implémenter** — ajouter à `Store` (après `load_entities`) :

```python
    def list_sets(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT s.id AS id, s.name AS name, s.description AS description, "
            "COUNT(d.id) AS document_count FROM source_sets s "
            "LEFT JOIN documents d ON d.set_id = s.id "
            "GROUP BY s.id, s.name, s.description ORDER BY s.id").fetchall()
        return [dict(r) for r in rows]

    def get_set(self, set_id: int) -> dict | None:
        s = self.conn.execute(
            "SELECT * FROM source_sets WHERE id = ?", (set_id,)).fetchone()
        if s is None:
            return None
        docs = self.conn.execute(
            "SELECT id, title, source_kind, source_ref, status, total_facts, "
            "unique_facts, duplicate_facts, created_at FROM documents "
            "WHERE set_id = ? ORDER BY id", (set_id,)).fetchall()
        out = dict(s)
        out["documents"] = [dict(r) for r in docs]
        return out
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_store.py -q` → PASS.
- [ ] **Step 5 : Commit**

```bash
git add nerve/store.py tests/test_store.py
git commit -m "feat(i4): store — list_sets/get_set"
```

---

## Task 2 : store.py — facts_for_set (lignes enrichies pour build_graph)

**Files:** Modify `nerve/store.py` ; Test `tests/test_store.py`

- [ ] **Step 1 : Test rouge** — ajouter à `tests/test_store.py` :

```python
def _seed_fact(st, doc_id, s_name, s_key, pred, o_name, o_key, conf=80):
    se = st.create_entity(doc_id, s_name, s_key)
    oe = st.create_entity(doc_id, o_name, o_key)
    return st.add_fact(doc_id, {"subject": s_name, "predicate": pred,
                                "object": o_name, "confidence": conf},
                       subject_entity_id=se, object_entity_id=oe)

def test_facts_for_set_enriched_rows(tmp_path):
    st = Store(str(tmp_path / "ffs.db"), embed_dim=3); st.init_db()
    s = st.create_set("S")
    d = st.create_document(s, "doc", "text")
    _seed_fact(st, d, "Cluny", "cluny", "fonde", "Abbaye", "abbaye", conf=90)
    _seed_fact(st, d, "Cluny", "cluny", "situe", "Bourgogne", "bourgogne", conf=40)
    rows = st.facts_for_set(s)
    assert len(rows) == 2
    r0 = rows[0]
    assert r0["s_key"] == "cluny" and r0["s_name"] == "Cluny"
    assert r0["o_key"] == "abbaye" and r0["predicate"] == "fonde"
    # filtre min_conf
    assert len(st.facts_for_set(s, min_conf=50)) == 1
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_store.py -q` → FAIL (`facts_for_set` absent).

- [ ] **Step 3 : Implémenter** — ajouter à `Store` (après `get_set`) :

```python
    _GRAPH_COLS = (
        "f.id AS fact_id, f.predicate AS predicate, f.confidence AS confidence, "
        "f.document_id AS document_id, "
        "se.normalized_key AS s_key, se.canonical_name AS s_name, "
        "se.mention_count AS s_mentions, "
        "oe.normalized_key AS o_key, oe.canonical_name AS o_name, "
        "oe.mention_count AS o_mentions")

    def facts_for_set(self, set_id: int, min_conf: int | None = None) -> list[dict]:
        sql = ("SELECT " + self._GRAPH_COLS + " FROM facts f "
               "JOIN documents d ON d.id = f.document_id "
               "JOIN entities se ON se.id = f.subject_entity_id "
               "JOIN entities oe ON oe.id = f.object_entity_id "
               "WHERE d.set_id = ? AND f.is_duplicate = 0")
        params: list = [set_id]
        if min_conf is not None:
            sql += " AND f.confidence >= ?"
            params.append(min_conf)
        sql += " ORDER BY f.id"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_store.py -q` → PASS.
- [ ] **Step 5 : Commit**

```bash
git add nerve/store.py tests/test_store.py
git commit -m "feat(i4): store — facts_for_set (lignes enrichies, INNER JOIN entities, filtre min_conf)"
```

---

## Task 3 : graph.py — build_graph (pur)

**Files:** Create `nerve/graph.py` ; Test `tests/test_graph.py`

- [ ] **Step 1 : Tests rouges** — créer `tests/test_graph.py` :

```python
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
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_graph.py -q` → FAIL (`No module named 'nerve.graph'`).

- [ ] **Step 3 : Implémenter** — créer `nerve/graph.py` :

```python
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
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_graph.py -q` → PASS.
- [ ] **Step 5 : Commit**

```bash
git add nerve/graph.py tests/test_graph.py
git commit -m "feat(i4): graph — build_graph (collapse par clé, dédup liens, libellé le + mentionné)"
```

---

## Task 4 : store.py — search_facts (KNN vec_facts + over-fetch filtre sets)

**Files:** Modify `nerve/store.py` ; Test `tests/test_store.py`

Note : sqlite-vec applique un filtre `WHERE` **après** le KNN ⇒ pour filtrer par set sans sous-retourner, on **sur-échantillonne** (`k' = max(k*10, 100)`) puis on filtre/tronque en Python.

- [ ] **Step 1 : Test rouge** — ajouter à `tests/test_store.py` :

```python
def test_search_facts_knn_and_set_filter(tmp_path):
    st = Store(str(tmp_path / "sf.db"), embed_dim=3); st.init_db()
    sa = st.create_set("A"); sb = st.create_set("B")
    da = st.create_document(sa, "da", "text"); db = st.create_document(sb, "db", "text")
    fa = st.add_fact(da, {"subject": "A", "predicate": "r", "object": "B"})
    st.add_fact_vector(fa, [1.0, 0.0, 0.0])
    fb = st.add_fact(db, {"subject": "C", "predicate": "r", "object": "D"})
    st.add_fact_vector(fb, [0.0, 1.0, 0.0])
    # requête proche de fa
    res = st.search_facts([1.0, 0.0, 0.0], k=1)
    assert res[0]["fact_id"] == fa and "distance" in res[0]
    # filtre set B alors que le + proche est fa (set A) -> on récupère fb (over-fetch)
    res_b = st.search_facts([1.0, 0.0, 0.0], k=1, sets=[sb])
    assert [r["fact_id"] for r in res_b] == [fb]
    assert res_b[0]["set_id"] == sb
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_store.py -q` → FAIL (`search_facts` absent).

- [ ] **Step 3 : Implémenter** — ajouter à `Store` (après `facts_for_set`) :

```python
    def search_facts(self, query_vec: list[float], k: int,
                     sets: list[int] | None = None) -> list[dict]:
        knn_k = k if not sets else max(k * 10, 100)
        rows = self.conn.execute(
            "SELECT v.fact_id AS fact_id, v.distance AS distance, "
            "f.subject AS subject, f.predicate AS predicate, f.object AS object, "
            "f.description AS description, f.document_id AS document_id, "
            "d.set_id AS set_id FROM vec_facts v "
            "JOIN facts f ON f.id = v.fact_id "
            "JOIN documents d ON d.id = f.document_id "
            "WHERE v.embedding MATCH ? AND k = ?",
            (sqlite_vec.serialize_float32(query_vec), knn_k)).fetchall()
        out: list[dict] = []
        for r in rows:
            if sets and r["set_id"] not in sets:
                continue
            out.append(dict(r))
            if len(out) >= k:
                break
        return out
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_store.py -q` → PASS.
- [ ] **Step 5 : Commit**

```bash
git add nerve/store.py tests/test_store.py
git commit -m "feat(i4): store — search_facts (KNN vec_facts + over-fetch filtre sets)"
```

---

## Task 5 : store.py — transverse (entities_by_key / entity_neighbors / facts_for_entities)

**Files:** Modify `nerve/store.py` ; Test `tests/test_store.py`

- [ ] **Step 1 : Tests rouges** — ajouter à `tests/test_store.py` :

```python
def test_entities_by_key_cross_document(tmp_path):
    st = Store(str(tmp_path / "ebk.db"), embed_dim=3); st.init_db()
    s = st.create_set("S")
    d1 = st.create_document(s, "d1", "text"); d2 = st.create_document(s, "d2", "text")
    e1 = st.create_entity(d1, "Cluny", "cluny")
    e2 = st.create_entity(d2, "cluny", "cluny")
    st.create_entity(d1, "Autre", "autre")
    got = {e["id"] for e in st.entities_by_key("cluny")}
    assert got == {e1, e2}

def test_entity_neighbors_knn(tmp_path):
    st = Store(str(tmp_path / "en.db"), embed_dim=3); st.init_db()
    s = st.create_set("S"); d = st.create_document(s, "d", "text")
    e1 = st.create_entity(d, "Cluny", "cluny"); st.add_entity_vector(e1, [1.0, 0.0, 0.0])
    e2 = st.create_entity(d, "Loin", "loin"); st.add_entity_vector(e2, [0.0, 1.0, 0.0])
    res = st.entity_neighbors([1.0, 0.0, 0.0], k=1)
    assert res[0]["entity_id"] == e1 and res[0]["normalized_key"] == "cluny"

def test_facts_for_entities(tmp_path):
    st = Store(str(tmp_path / "ffe.db"), embed_dim=3); st.init_db()
    s = st.create_set("S"); d = st.create_document(s, "d", "text")
    se = st.create_entity(d, "Cluny", "cluny"); oe = st.create_entity(d, "Abbaye", "abbaye")
    other = st.create_entity(d, "X", "x")
    f = st.add_fact(d, {"subject": "Cluny", "predicate": "est", "object": "Abbaye"},
                    subject_entity_id=se, object_entity_id=oe)
    st.add_fact(d, {"subject": "X", "predicate": "p", "object": "X"},
                subject_entity_id=other, object_entity_id=other)
    rows = st.facts_for_entities([se])
    assert [r["fact_id"] for r in rows] == [f]
    assert rows[0]["s_key"] == "cluny" and rows[0]["o_key"] == "abbaye"
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_store.py -q` → FAIL (`entities_by_key` absent).

- [ ] **Step 3 : Implémenter** — ajouter à `Store` (après `search_facts`) :

```python
    def entities_by_key(self, normalized_key: str,
                        sets: list[int] | None = None) -> list[dict]:
        sql = ("SELECT e.id AS id, e.normalized_key AS normalized_key, "
               "e.canonical_name AS canonical_name, e.document_id AS document_id, "
               "d.set_id AS set_id FROM entities e "
               "JOIN documents d ON d.id = e.document_id WHERE e.normalized_key = ?")
        params: list = [normalized_key]
        if sets:
            sql += " AND d.set_id IN (%s)" % ",".join("?" * len(sets))
            params += list(sets)
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def entity_neighbors(self, query_vec: list[float], k: int,
                         sets: list[int] | None = None) -> list[dict]:
        knn_k = k if not sets else max(k * 10, 100)
        rows = self.conn.execute(
            "SELECT v.entity_id AS entity_id, v.distance AS distance, "
            "e.normalized_key AS normalized_key, e.canonical_name AS canonical_name, "
            "d.set_id AS set_id FROM vec_entities v "
            "JOIN entities e ON e.id = v.entity_id "
            "JOIN documents d ON d.id = e.document_id "
            "WHERE v.embedding MATCH ? AND k = ?",
            (sqlite_vec.serialize_float32(query_vec), knn_k)).fetchall()
        out: list[dict] = []
        for r in rows:
            if sets and r["set_id"] not in sets:
                continue
            out.append(dict(r))
            if len(out) >= k:
                break
        return out

    def facts_for_entities(self, entity_ids: list[int],
                           min_conf: int | None = None) -> list[dict]:
        if not entity_ids:
            return []
        ph = ",".join("?" * len(entity_ids))
        sql = ("SELECT " + self._GRAPH_COLS + " FROM facts f "
               "JOIN entities se ON se.id = f.subject_entity_id "
               "JOIN entities oe ON oe.id = f.object_entity_id "
               "WHERE f.is_duplicate = 0 AND "
               f"(f.subject_entity_id IN ({ph}) OR f.object_entity_id IN ({ph}))")
        params: list = list(entity_ids) + list(entity_ids)
        if min_conf is not None:
            sql += " AND f.confidence >= ?"
            params.append(min_conf)
        sql += " ORDER BY f.id"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_store.py -q` → PASS.
- [ ] **Step 5 : Commit**

```bash
git add nerve/store.py tests/test_store.py
git commit -m "feat(i4): store — transverse (entities_by_key/entity_neighbors/facts_for_entities)"
```

---

## Task 6 : api.py — GET /api/sets + /api/sets/{id}

**Files:** Modify `nerve/api.py` ; Test `tests/test_api.py`

- [ ] **Step 1 : Tests rouges** — ajouter à `tests/test_api.py` :

```python
def test_sets_list_and_detail(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "3")
    import importlib, nerve.api as api
    importlib.reload(api)
    s = api.store.create_set("S")
    api.store.create_document(s, "d", "text")
    client = TestClient(api.app)
    lst = client.get("/api/sets").json()
    assert any(x["id"] == s and x["document_count"] == 1 for x in lst)
    detail = client.get(f"/api/sets/{s}").json()
    assert detail["name"] == "S" and len(detail["documents"]) == 1
    assert client.get("/api/sets/9999").status_code == 404
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_api.py::test_sets_list_and_detail -q` → FAIL (404 : routes absentes).

- [ ] **Step 3 : Implémenter** —

Dans `nerve/api.py`, remplacer la ligne d'import FastAPI :

```python
from fastapi import FastAPI, HTTPException, Form, UploadFile, File
```

par :

```python
from fastapi import FastAPI, HTTPException, Form, UploadFile, File, Query
```

Et ajouter, après l'import `from nerve.ingest import ingest_upload, IngestError` :

```python
from nerve.graph import build_graph
from nerve.entities import normalized_key
from nerve.embeddings import embed
```

Ajouter ces routes dans `nerve/api.py`, **après** `get_facts` (la route `GET /api/documents/{doc_id}/facts`) et **avant** la route racine `GET /` :

```python
@app.get("/api/sets")
def list_sets():
    return store.list_sets()

@app.get("/api/sets/{set_id}")
def get_set(set_id: int):
    s = store.get_set(set_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Set introuvable")
    return s
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_api.py -q` → PASS.
- [ ] **Step 5 : Commit**

```bash
git add nerve/api.py tests/test_api.py
git commit -m "feat(i4): api — GET /api/sets + /api/sets/{id} (+ imports graph/entities/embeddings/Query)"
```

---

## Task 7 : api.py — GET /api/sets/{id}/graph

**Files:** Modify `nerve/api.py` ; Test `tests/test_api.py`

- [ ] **Step 1 : Test rouge** — ajouter à `tests/test_api.py` :

```python
def test_set_graph(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "3")
    import importlib, nerve.api as api
    importlib.reload(api)
    s = api.store.create_set("S")
    d1 = api.store.create_document(s, "d1", "text")
    d2 = api.store.create_document(s, "d2", "text")
    for d in (d1, d2):                                  # "Cluny" présent dans 2 documents
        se = api.store.create_entity(d, "Cluny", "cluny")
        oe = api.store.create_entity(d, "Abbaye", "abbaye")
        api.store.add_fact(d, {"subject": "Cluny", "predicate": "est", "object": "Abbaye"},
                           subject_entity_id=se, object_entity_id=oe)
    client = TestClient(api.app)
    g = client.get(f"/api/sets/{s}/graph").json()
    assert sorted(n["id"] for n in g["nodes"]) == ["abbaye", "cluny"]   # collapse cross-doc
    assert len(g["links"]) == 1
    assert client.get("/api/sets/9999/graph").status_code == 404
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_api.py::test_set_graph -q` → FAIL (route absente).

- [ ] **Step 3 : Implémenter** — ajouter à `nerve/api.py` (après `get_set`) :

```python
@app.get("/api/sets/{set_id}/graph")
def set_graph(set_id: int, min_conf: int | None = None):
    if store.get_set(set_id) is None:
        raise HTTPException(status_code=404, detail="Set introuvable")
    return build_graph(store.facts_for_set(set_id, min_conf))
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_api.py -q` → PASS.
- [ ] **Step 5 : Commit**

```bash
git add nerve/api.py tests/test_api.py
git commit -m "feat(i4): api — GET /api/sets/{id}/graph (facts_for_set + build_graph)"
```

---

## Task 8 : api.py — GET /api/search

**Files:** Modify `nerve/api.py` ; Test `tests/test_api.py`

- [ ] **Step 1 : Test rouge** — ajouter à `tests/test_api.py` :

```python
def test_search_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "3")
    import importlib, nerve.api as api
    importlib.reload(api)
    s = api.store.create_set("S"); d = api.store.create_document(s, "d", "text")
    fa = api.store.add_fact(d, {"subject": "Cluny", "predicate": "r", "object": "Abbaye"})
    api.store.add_fact_vector(fa, [1.0, 0.0, 0.0])
    fb = api.store.add_fact(d, {"subject": "X", "predicate": "r", "object": "Y"})
    api.store.add_fact_vector(fb, [0.0, 1.0, 0.0])
    async def fake_embed(cfg, texts, *, client=None):
        return [[1.0, 0.0, 0.0]]
    monkeypatch.setattr(api, "embed", fake_embed)
    client = TestClient(api.app)
    res = client.get("/api/search?q=cluny&k=1").json()
    assert res["results"][0]["fact_id"] == fa
    assert client.get("/api/search?q=").status_code == 400
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_api.py::test_search_endpoint -q` → FAIL (route absente).

- [ ] **Step 3 : Implémenter** — ajouter à `nerve/api.py` (après `set_graph`) :

```python
@app.get("/api/search")
async def search(q: str, sets: list[int] | None = Query(None), k: int = 20):
    if not q.strip():
        raise HTTPException(status_code=400, detail="q requis")
    vec = (await embed(cfg.embed, [q]))[0]
    return {"results": store.search_facts(vec, k, sets)}
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_api.py -q` → PASS.
- [ ] **Step 5 : Commit**

```bash
git add nerve/api.py tests/test_api.py
git commit -m "feat(i4): api — GET /api/search (embed q + KNN vec_facts)"
```

---

## Task 9 : api.py — GET /api/transverse

**Files:** Modify `nerve/api.py` ; Test `tests/test_api.py`

Décision déterministe : le handler n'interroge le **voisinage vectoriel que si `entities_by_key` est non vide**. Une entité absente du corpus → graphe vide (pas d'erreur, pas de voisinage parasite).

- [ ] **Step 1 : Test rouge** — ajouter à `tests/test_api.py` :

```python
def test_transverse_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "3")
    import importlib, nerve.api as api
    importlib.reload(api)
    s = api.store.create_set("S")
    d1 = api.store.create_document(s, "d1", "text")
    d2 = api.store.create_document(s, "d2", "text")
    for d in (d1, d2):                                  # "Cluny" dans 2 documents
        se = api.store.create_entity(d, "Cluny", "cluny"); api.store.add_entity_vector(se, [1.0, 0.0, 0.0])
        oe = api.store.create_entity(d, "Abbaye", "abbaye"); api.store.add_entity_vector(oe, [0.0, 1.0, 0.0])
        api.store.add_fact(d, {"subject": "Cluny", "predicate": "est", "object": "Abbaye"},
                           subject_entity_id=se, object_entity_id=oe)
    async def fake_embed(cfg, texts, *, client=None):
        return [[1.0, 0.0, 0.0]]                        # proche des entités "cluny"
    monkeypatch.setattr(api, "embed", fake_embed)
    client = TestClient(api.app)
    g = client.get("/api/transverse?entity=Cluny&k=5").json()
    keys = {n["id"] for n in g["nodes"]}
    assert "cluny" in keys and "abbaye" in keys
    assert len(g["links"]) >= 1
    # entité absente -> graphe vide (entities_by_key vide -> pas de voisinage)
    empty = client.get("/api/transverse?entity=Inconnu&k=1").json()
    assert empty == {"nodes": [], "links": []}
    assert client.get("/api/transverse?entity=").status_code == 400
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_api.py::test_transverse_endpoint -q` → FAIL (route absente).

- [ ] **Step 3 : Implémenter** — ajouter à `nerve/api.py` (après `search`) :

```python
@app.get("/api/transverse")
async def transverse(entity: str, sets: list[int] | None = Query(None),
                     min_conf: int | None = None, k: int = 10):
    if not entity.strip():
        raise HTTPException(status_code=400, detail="entity requis")
    occ = store.entities_by_key(normalized_key(entity), sets)
    if not occ:
        return {"nodes": [], "links": []}              # entité absente -> graphe vide
    ids = {e["id"] for e in occ}
    vec = (await embed(cfg.embed, [entity]))[0]
    for n in store.entity_neighbors(vec, k, sets):
        ids.add(n["entity_id"])
    return build_graph(store.facts_for_entities(list(ids), min_conf))
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_api.py -q` → PASS.
- [ ] **Step 5 : Commit**

```bash
git add nerve/api.py tests/test_api.py
git commit -m "feat(i4): api — GET /api/transverse (clé normalisée + voisinage vec_entities -> build_graph)"
```

---

## Task 10 : web/index.html — UI minimale (sidebar sets/docs, recherche, transverse)

**Files:** Modify `nerve/web/index.html` (pas de test auto — vérifié au smoke + suite verte)

- [ ] **Step 1 : Implémenter** —

(a) Dans le `<style>`, **remplacer** la règle `#graph` pour laisser la place de la sidebar et ajouter les styles de la sidebar :

Remplacer :

```css
  #graph{position:absolute;left:0;right:0;bottom:0;top:66px}
```

par :

```css
  #graph{position:absolute;left:260px;right:0;bottom:0;top:66px}
  #side{position:absolute;left:0;top:66px;bottom:0;width:260px;overflow:auto;
        background:#fff;border-right:1px solid var(--line);padding:10px;font-size:13px}
  #side h3{margin:10px 0 4px;font-size:12px;text-transform:uppercase;color:var(--blue)}
  #side input{width:100%;padding:6px;margin:2px 0;border:1px solid var(--line);border-radius:5px}
  #side button{width:100%;padding:6px;margin:2px 0;background:#2C77B6;color:#fff;border:0;border-radius:5px;cursor:pointer}
  #side .item{padding:4px 6px;border-radius:5px;cursor:pointer}
  #side .item:hover{background:var(--paper)}
  #results .r{padding:4px 6px;border-bottom:1px solid var(--line);font-size:12px}
```

(b) Juste après `<div id="graph"></div>`, ajouter la sidebar :

```html
  <div id="side">
    <h3>Recherche</h3>
    <input id="q" placeholder="recherche sémantique…">
    <button id="searchBtn">Chercher</button>
    <div id="results"></div>
    <h3>Transverse</h3>
    <input id="ent" placeholder="entité (ex. Cluny)">
    <button id="transBtn">Sous-graphe</button>
    <h3>Sets</h3>
    <div id="sets"></div>
  </div>
```

(c) Dans le `<script>`, remplacer la ligne `nodeLabel` pour afficher le libellé canonique quand il existe :

Remplacer :

```javascript
  .nodeLabel(n=> escapeHtml(n.id))
```

par :

```javascript
  .nodeLabel(n=> escapeHtml(n.label||n.id))
```

(d) À la fin du `<script>` (juste avant `</script>`), ajouter la logique de navigation. Le rendu réutilise `addFact` (document) ou injecte directement `{nodes, links}` (set/transverse). Le contenu texte issu du LLM est inséré via `textContent` (jamais `innerHTML`), et les conteneurs sont vidés via `replaceChildren()` :

```javascript
// --- niveaux Set / Transverse / Recherche (I-4) ---
function renderGraph(data){
  linkKeys=new Set();
  G.graphData({nodes:data.nodes||[], links:data.links||[]});
}
async function loadSets(){
  const sets=await (await fetch("/api/sets")).json();
  const box=document.getElementById("sets"); box.replaceChildren();
  sets.forEach(s=>{
    const el=document.createElement("div"); el.className="item";
    el.textContent=`${s.name} (${s.document_count})`;
    el.onclick=()=>openSet(s.id);
    box.appendChild(el);
  });
}
async function openSet(id){
  renderGraph(await (await fetch(`/api/sets/${id}/graph`)).json());
  const detail=await (await fetch(`/api/sets/${id}`)).json();
  const sub=document.createElement("div");
  detail.documents.forEach(d=>{
    const el=document.createElement("div"); el.className="item"; el.style.paddingLeft="16px";
    el.textContent=`· ${d.title}`;
    el.onclick=(e)=>{ e.stopPropagation(); openDocument(d.id); };
    sub.appendChild(el);
  });
  document.getElementById("sets").appendChild(sub);
}
async function openDocument(id){
  const facts=await (await fetch(`/api/documents/${id}/facts`)).json();
  nodes=new Map(); links=[]; linkKeys=new Set();
  facts.forEach(addFact); redraw();
}
document.getElementById("searchBtn").addEventListener("click", async ()=>{
  const q=document.getElementById("q").value.trim(); if(!q) return;
  const res=await (await fetch(`/api/search?q=${encodeURIComponent(q)}`)).json();
  const box=document.getElementById("results"); box.replaceChildren();
  (res.results||[]).forEach(r=>{
    const el=document.createElement("div"); el.className="r";
    el.textContent=`${r.subject} · ${r.predicate} · ${r.object}`;
    box.appendChild(el);
  });
});
document.getElementById("transBtn").addEventListener("click", async ()=>{
  const ent=document.getElementById("ent").value.trim(); if(!ent) return;
  renderGraph(await (await fetch(`/api/transverse?entity=${encodeURIComponent(ent)}`)).json());
});
loadSets();
```

- [ ] **Step 2 : Vérifier la suite complète**

Run: `uv run pytest -q`
Expected: PASS (suite I-1…I-3 + nouveaux tests I-4).

- [ ] **Step 3 : Commit**

```bash
git add nerve/web/index.html
git commit -m "feat(i4): front — UI minimale (navigation sets/docs, recherche, transverse)"
```

---

## Vérifications réelles (smoke — Ollama requis)

1. `rm -f data/nerve.db*` (repartir propre).
2. `uv run nerve` → coller plusieurs textes (chacun crée un document dans le set par défaut) partageant une entité (ex. « Cluny »).
3. **Sets** : la sidebar liste les sets ; clic set → graphe agrégé (entité partagée = **un seul nœud**) + liste des documents ; clic document → graphe du document.
4. **Recherche** : saisir une requête → liste de faits classés par similarité (`vec_facts`).
5. **Transverse** : saisir « Cluny » → sous-graphe reliant les occurrences cross-document + voisins sémantiques (`vec_entities`), borné.
6. (Optionnel multi-sets) Créer un 2e set via `POST /api/documents` avec `set_name` distinct, puis vérifier le filtre `sets` de `/api/search` et `/api/transverse`.

---

## Self-Review (couverture spec → tâches)

- Spec §4 `graph.py` (build_graph, collapse par clé, dédup liens, libellé le + mentionné, exclusion sans clé) → **T3**.
- Spec §5 store (list_sets/get_set, facts_for_set, search_facts, entities_by_key/entity_neighbors/facts_for_entities) → **T1, T2, T4, T5**.
- Spec §6 api (sets, set-graph, search, transverse) → **T6, T7, T8, T9**.
- Spec §7 flux transverse (normalisation requête, identité par clé, voisinage vectoriel, bornage, graphe vide si absente) → **T9** (handler) + **T5** (requêtes).
- Spec §8 UI minimale (sidebar sets→docs→graphe, recherche, transverse, flux SSE conservé) → **T10**.
- Spec §9 tests sans réseau → couverts T1-T9 ; bout-en-bout réel = smoke.
- Spec §11 risques : over-fetch KNN sous filtre `sets` (vérifié empiriquement v0.1.9) → **T4/T5** ; faits sans `*_entity_id` exclus (INNER JOIN) → **T2/T5** ; normalisation via `entities.normalized_key` (pas de ré-implémentation) → **T9**.

Cohérence des signatures/types : lignes enrichies `{fact_id, predicate, confidence, document_id, s_key, s_name, s_mentions, o_key, o_name, o_mentions}` produites par `facts_for_set` (T2) et `facts_for_entities` (T5, via `_GRAPH_COLS` partagé) et consommées par `build_graph` (T3) ; `entities_by_key` → clé `id`, `entity_neighbors` → clé `entity_id` (le handler transverse T9 lit les deux) ; `search_facts`/`entity_neighbors` over-fetch `max(k*10,100)` sous filtre `sets`. Pas de placeholder.
