# Plan 2 / I-5 · Visualisation complète (graphology) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Donner à la vue graphe une couche analytique (graphology : communautés louvain + centralité) et esthétique complète (4 modes de couleur, taille, confiance/passerelles/étiquettes d'arêtes, cartes de fait, thèmes clair/sombre), en s'appuyant sur un vrai type de nœud entité/valeur étiqueté par le LLM.

**Architecture:** Couche données (extraction du type → `entities.kind` ; payload `build_graph` enrichi `kind`/`sets`/`confidence`/`is_bridge`) puis couche front (extraction de `graph.js`/`theme.css` de `index.html` ; graphology côté client ; aucun nouvel endpoint). Les endpoints I-4 sont inchangés ; seul `build_graph` enrichit la charge utile.

**Tech Stack:** Python 3.11+, FastAPI, SQLite + sqlite-vec, force-graph@1.43.5 (rendu), graphology@0.25.4 + graphology-library@0.7.0 (analytics, CDN), pytest (asyncio auto).

**Réf. spec :** `docs/superpowers/specs/2026-06-14-nerve-I5-visualization-design.md`.

---

## File Structure

- **Modify** `nerve/store.py` — `entities.kind` (DDL) ; `create_entity(kind=…)` ; `promote_entity_kind` ; `_GRAPH_COLS` += `s_kind`/`o_kind`/`set_id` ; `facts_for_entities` JOIN documents.
- **Modify** `nerve/graph.py` — `build_graph` enrichit nœuds (`kind`, `sets`) et liens (`confidence`, `is_bridge`).
- **Modify** `nerve/extract.py` — `FACT_SCHEMA` += `subject_kind`/`object_kind` ; prompt (définition entité/valeur).
- **Modify** `nerve/entities.py` — `EntityResolver.resolve(name, kind=…)` (entity domine value).
- **Modify** `nerve/pipeline.py` — passe le `kind` du fait à `resolve`.
- **Modify** `nerve/api.py` — routes `GET /graph.js` et `GET /theme.css` (`FileResponse`).
- **Create** `nerve/web/theme.css` — palette scriptorium clair + sombre, styles contrôles/légende/carte.
- **Create** `nerve/web/graph.js` — module de visualisation (force-graph + graphology + modes + thème).
- **Modify** `nerve/web/index.html` — markup minimal + barre de contrôles + conteneurs légende/carte ; liens `theme.css`/`graph.js`.
- **Modify** `tests/test_store.py`, `tests/test_graph.py`, `tests/test_extract.py`, `tests/test_entities.py`, `tests/test_pipeline.py`, `tests/test_api.py`.

Conventions reprises : tests sans réseau (`monkeypatch` sur `embed`/`stream_chat`), `asyncio_mode=auto`, `TestClient` + `importlib.reload(api)` après `setenv`. **DB jetable** : le schéma change (colonne `kind`) → `rm -f data/nerve.db*` avant tout smoke.

---

## Task 1 : store.py — entities.kind (colonne + create_entity + promote_entity_kind)

**Files:** Modify `nerve/store.py` ; Test `tests/test_store.py`

- [ ] **Step 1 : Test rouge** — ajouter à `tests/test_store.py` :

```python
def test_entity_kind_default_and_promote(tmp_path):
    st = Store(str(tmp_path / "kind.db"), embed_dim=3); st.init_db()
    d = st.create_document(st.create_set("S"), "doc", "text")
    def kind_of(eid):
        return st.conn.execute("SELECT kind FROM entities WHERE id=?", (eid,)).fetchone()[0]
    e_def = st.create_entity(d, "Cluny", "cluny")               # défaut -> entity
    e_val = st.create_entity(d, "910", "910", kind="value")     # explicite value
    assert kind_of(e_def) == "entity" and kind_of(e_val) == "value"
    st.promote_entity_kind(e_val)                               # value -> entity (idempotent)
    assert kind_of(e_val) == "entity"
    st.promote_entity_kind(e_def)                               # déjà entity -> no-op
    assert kind_of(e_def) == "entity"
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_store.py::test_entity_kind_default_and_promote -q` → FAIL (`kind` absent / `promote_entity_kind` absent).

- [ ] **Step 3 : Implémenter** —

(a) Dans la constante `SCHEMA`, la table `entities` : remplacer

```python
CREATE TABLE IF NOT EXISTS entities (
  id INTEGER PRIMARY KEY,
  document_id INTEGER REFERENCES documents(id),
  canonical_name TEXT NOT NULL, normalized_key TEXT NOT NULL,
  mention_count INTEGER DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now'))
);
```

par

```python
CREATE TABLE IF NOT EXISTS entities (
  id INTEGER PRIMARY KEY,
  document_id INTEGER REFERENCES documents(id),
  canonical_name TEXT NOT NULL, normalized_key TEXT NOT NULL,
  mention_count INTEGER DEFAULT 1,
  kind TEXT DEFAULT 'entity',
  created_at TEXT DEFAULT (datetime('now'))
);
```

(b) Remplacer la méthode `create_entity` :

```python
    def create_entity(self, document_id: int, canonical_name: str,
                      normalized_key: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO entities(document_id, canonical_name, normalized_key) "
            "VALUES (?, ?, ?)", (document_id, canonical_name, normalized_key))
        self.conn.commit()
        return cur.lastrowid
```

par

```python
    def create_entity(self, document_id: int, canonical_name: str,
                      normalized_key: str, kind: str = "entity") -> int:
        cur = self.conn.execute(
            "INSERT INTO entities(document_id, canonical_name, normalized_key, kind) "
            "VALUES (?, ?, ?, ?)", (document_id, canonical_name, normalized_key, kind))
        self.conn.commit()
        return cur.lastrowid

    def promote_entity_kind(self, entity_id: int) -> None:
        """Promotion vers 'entity' (entity domine value) ; no-op si déjà 'entity'."""
        self.conn.execute(
            "UPDATE entities SET kind = 'entity' WHERE id = ? AND kind = 'value'",
            (entity_id,))
        self.conn.commit()
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_store.py -q` → PASS.
- [ ] **Step 5 : Commit**

```bash
git add nerve/store.py tests/test_store.py
git commit -m "feat(i5): store — entities.kind (colonne + create_entity kind + promote_entity_kind)"
```

---

## Task 2 : store.py — _GRAPH_COLS (s_kind/o_kind/set_id) + facts_for_entities JOIN documents

**Files:** Modify `nerve/store.py` ; Test `tests/test_store.py`

- [ ] **Step 1 : Test rouge** — ajouter à `tests/test_store.py` :

```python
def test_graph_cols_expose_kind_and_set(tmp_path):
    st = Store(str(tmp_path / "gc.db"), embed_dim=3); st.init_db()
    s = st.create_set("S"); d = st.create_document(s, "doc", "text")
    se = st.create_entity(d, "Cluny", "cluny", kind="entity")
    oe = st.create_entity(d, "910", "910", kind="value")
    f = st.add_fact(d, {"subject": "Cluny", "predicate": "fonde", "object": "910"},
                    subject_entity_id=se, object_entity_id=oe)
    r0 = st.facts_for_set(s)[0]
    assert r0["s_kind"] == "entity" and r0["o_kind"] == "value" and r0["set_id"] == s
    # facts_for_entities expose aussi set_id (JOIN documents ajouté)
    r1 = st.facts_for_entities([se])[0]
    assert r1["set_id"] == s and r1["s_kind"] == "entity" and r1["fact_id"] == f
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_store.py::test_graph_cols_expose_kind_and_set -q` → FAIL (clés `s_kind`/`set_id` absentes).

- [ ] **Step 3 : Implémenter** —

(a) Remplacer la constante `_GRAPH_COLS` :

```python
    _GRAPH_COLS = (
        "f.id AS fact_id, f.predicate AS predicate, f.confidence AS confidence, "
        "f.document_id AS document_id, "
        "se.normalized_key AS s_key, se.canonical_name AS s_name, "
        "se.mention_count AS s_mentions, "
        "oe.normalized_key AS o_key, oe.canonical_name AS o_name, "
        "oe.mention_count AS o_mentions")
```

par

```python
    _GRAPH_COLS = (
        "f.id AS fact_id, f.predicate AS predicate, f.confidence AS confidence, "
        "f.document_id AS document_id, d.set_id AS set_id, "
        "se.normalized_key AS s_key, se.canonical_name AS s_name, "
        "se.mention_count AS s_mentions, se.kind AS s_kind, "
        "oe.normalized_key AS o_key, oe.canonical_name AS o_name, "
        "oe.mention_count AS o_mentions, oe.kind AS o_kind")
```

(`facts_for_set` joint déjà `documents d` → `d.set_id` résout.)

(b) Dans `facts_for_entities`, ajouter le JOIN documents (nécessaire à `d.set_id`). Remplacer :

```python
        sql = ("SELECT " + self._GRAPH_COLS + " FROM facts f "
               "JOIN entities se ON se.id = f.subject_entity_id "
               "JOIN entities oe ON oe.id = f.object_entity_id "
               "WHERE f.is_duplicate = 0 AND "
               f"(f.subject_entity_id IN ({ph}) OR f.object_entity_id IN ({ph}))")
```

par

```python
        sql = ("SELECT " + self._GRAPH_COLS + " FROM facts f "
               "JOIN documents d ON d.id = f.document_id "
               "JOIN entities se ON se.id = f.subject_entity_id "
               "JOIN entities oe ON oe.id = f.object_entity_id "
               "WHERE f.is_duplicate = 0 AND "
               f"(f.subject_entity_id IN ({ph}) OR f.object_entity_id IN ({ph}))")
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_store.py -q` → PASS.
- [ ] **Step 5 : Commit**

```bash
git add nerve/store.py tests/test_store.py
git commit -m "feat(i5): store — _GRAPH_COLS expose s_kind/o_kind/set_id ; facts_for_entities JOIN documents"
```

---

## Task 3 : graph.py — build_graph enrichi (kind, sets, confidence, is_bridge)

**Files:** Modify `nerve/graph.py` ; Test `tests/test_graph.py`

- [ ] **Step 1 : Tests rouges** — ajouter à `tests/test_graph.py` (le helper `_row` existant n'a pas `set_id`/`*_kind` ; on les passe explicitement ici) :

```python
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
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_graph.py -q` → FAIL (clés `kind`/`sets`/`is_bridge` absentes).

- [ ] **Step 3 : Implémenter** — remplacer **tout** le contenu de `nerve/graph.py` par :

```python
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
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_graph.py -q` → PASS (les 3 tests I-4 existants + les 4 nouveaux).
- [ ] **Step 5 : Commit**

```bash
git add nerve/graph.py tests/test_graph.py
git commit -m "feat(i5): graph — build_graph enrichi (kind entity-domine-value, sets, confidence, is_bridge)"
```

---

## Task 4 : extract.py — subject_kind/object_kind (schéma + prompt)

**Files:** Modify `nerve/extract.py` ; Test `tests/test_extract.py`

- [ ] **Step 1 : Test rouge** — ajouter à `tests/test_extract.py` :

```python
def test_fact_schema_has_kind_fields():
    from nerve.extract import FACT_SCHEMA
    props = FACT_SCHEMA["properties"]
    assert props["subject_kind"]["enum"] == ["entity", "value"]
    assert props["object_kind"]["enum"] == ["entity", "value"]
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_extract.py::test_fact_schema_has_kind_fields -q` → FAIL (`subject_kind` absent).

- [ ] **Step 3 : Implémenter** —

(a) Dans `FACT_SCHEMA["properties"]`, ajouter deux champs (après `"object": {"type": "string"},`) :

```python
        "subject_kind": {"type": "string", "enum": ["entity", "value"]},
        "object_kind": {"type": "string", "enum": ["entity", "value"]},
```

(b) Dans `SYSTEM_PROMPT`, ajouter — juste avant la dernière phrase « Pour chaque fait : … » — ce paragraphe (concaténation de chaîne, garder le style existant) :

```python
    "Pour subject ET object, indique aussi son type via subject_kind / object_kind : "
    "« entity » = entité nommée (personne, lieu, organisation, œuvre, concept réifié) ; "
    "« value » = valeur littérale (date, nombre, mesure, durée, quantité, proportion). "
    "Ex. (Cluny, fonde, 910) -> subject_kind=entity, object_kind=value.\n\n"
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_extract.py -q` → PASS.
- [ ] **Step 5 : Commit**

```bash
git add nerve/extract.py tests/test_extract.py
git commit -m "feat(i5): extract — type subject_kind/object_kind (schéma + prompt entité/valeur)"
```

---

## Task 5 : entities.py — resolve(kind) + promotion entity-domine-value

**Files:** Modify `nerve/entities.py` ; Test `tests/test_entities.py`

- [ ] **Step 1 : Test rouge** — ajouter à `tests/test_entities.py` (réutilise le helper `_emb` du fichier) :

```python
async def test_resolver_assigns_and_promotes_kind(tmp_path):
    st = Store(str(tmp_path / "k.db"), embed_dim=2); st.init_db()
    doc = st.create_document(st.create_set("S"), "d", "text")
    def kind_of(eid):
        return st.conn.execute("SELECT kind FROM entities WHERE id=?", (eid,)).fetchone()[0]
    r = EntityResolver(st, doc, _emb({"910": [1.0, 0.0], "Cluny": [0.0, 1.0]}), threshold=0.9)
    v = await r.resolve("910", kind="value")
    assert kind_of(v) == "value"
    v2 = await r.resolve("910", kind="entity")     # même clé -> promotion value->entity
    assert v2 == v and kind_of(v) == "entity"
    e = await r.resolve("Cluny", kind="entity")
    assert kind_of(e) == "entity"
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_entities.py::test_resolver_assigns_and_promotes_kind -q` → FAIL (`resolve()` n'accepte pas `kind`).

- [ ] **Step 3 : Implémenter** — remplacer la méthode `resolve` de `EntityResolver` :

```python
    async def resolve(self, name: str) -> int:
        key = normalized_key(name)
        if key in self._by_key:
            eid = self._by_key[key]
            self.store.bump_entity_mention(eid)
            self._note(eid, name)
            return eid
        vec = await self.embed_fn(name)
        eid = self._match(key, vec)
        if eid is None:
            eid = self.store.create_entity(self.doc, canonical_name=name, normalized_key=key)
            self.store.add_entity_vector(eid, vec)
            self._entities.append((eid, vec, key))
            self._surface[eid] = Counter()
        else:
            self.store.bump_entity_mention(eid)
        self._by_key[key] = eid
        self._note(eid, name)
        return eid
```

par

```python
    async def resolve(self, name: str, kind: str = "entity") -> int:
        key = normalized_key(name)
        if key in self._by_key:
            eid = self._by_key[key]
            self.store.bump_entity_mention(eid)
            if kind == "entity":
                self.store.promote_entity_kind(eid)
            self._note(eid, name)
            return eid
        vec = await self.embed_fn(name)
        eid = self._match(key, vec)
        if eid is None:
            eid = self.store.create_entity(self.doc, canonical_name=name,
                                           normalized_key=key, kind=kind)
            self.store.add_entity_vector(eid, vec)
            self._entities.append((eid, vec, key))
            self._surface[eid] = Counter()
        else:
            self.store.bump_entity_mention(eid)
            if kind == "entity":
                self.store.promote_entity_kind(eid)
        self._by_key[key] = eid
        self._note(eid, name)
        return eid
```

Note : le store minimal de `test_resolver_preload_reuses_known_entity` (clé déjà connue) n'appelle `promote_entity_kind` que si `kind == "entity"` ; ce test appelle `resolve("Cluny")` (défaut `entity`) → il faut que son `_Store` factice expose `promote_entity_kind`. Ajouter cette méthode au faux store de ce test :

Dans `tests/test_entities.py`, dans `test_resolver_preload_reuses_known_entity`, la classe `_Store` :

```python
    class _Store:                                       # store minimal (pas de DB)
        def bump_entity_mention(self, eid): pass
        def set_entity_canonical(self, eid, name): pass
```

devient

```python
    class _Store:                                       # store minimal (pas de DB)
        def bump_entity_mention(self, eid): pass
        def set_entity_canonical(self, eid, name): pass
        def promote_entity_kind(self, eid): pass
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_entities.py -q` → PASS.
- [ ] **Step 5 : Commit**

```bash
git add nerve/entities.py tests/test_entities.py
git commit -m "feat(i5): entities — resolve(kind) + promotion entity-domine-value"
```

---

## Task 6 : pipeline.py — passer le kind du fait à resolve

**Files:** Modify `nerve/pipeline.py` ; Test `tests/test_pipeline.py`

- [ ] **Step 1 : Test rouge** — ajouter à `tests/test_pipeline.py` :

```python
async def fake_stream_kinds(cfg, messages, **kw):
    yield ('[{"subject":"Cluny","predicate":"fonde","object":"910",'
           '"subject_kind":"entity","object_kind":"value"}]')

async def fake_embed_kinds(cfg, texts, **kw):
    table = {"Cluny": [1.0, 0.0, 0.0], "910": [0.0, 1.0, 0.0],
             "Cluny fonde 910": [0.0, 0.0, 1.0]}
    return [table[t] for t in texts]

async def test_run_extraction_persists_kind(tmp_path, monkeypatch):
    monkeypatch.setattr(pipe, "stream_chat", fake_stream_kinds)
    monkeypatch.setattr(pipe, "embed", fake_embed_kinds)
    st = Store(str(tmp_path / "k.db"), embed_dim=3); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text")
    [e async for e in pipe.run_extraction(load_config(), st, doc_id, [("t", "")])]
    rows = st.conn.execute("SELECT canonical_name, kind FROM entities").fetchall()
    kinds = {r["canonical_name"]: r["kind"] for r in rows}
    assert kinds["Cluny"] == "entity" and kinds["910"] == "value"
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_pipeline.py::test_run_extraction_persists_kind -q` → FAIL (`910` créé en `entity` par défaut, pas `value`).

- [ ] **Step 3 : Implémenter** — dans `nerve/pipeline.py`, remplacer les deux lignes de résolution :

```python
                        sid = await resolver.resolve(fact["subject"])
                        oid = await resolver.resolve(fact["object"])
```

par

```python
                        sid = await resolver.resolve(
                            fact["subject"], _kind(fact.get("subject_kind")))
                        oid = await resolver.resolve(
                            fact["object"], _kind(fact.get("object_kind")))
```

et ajouter, juste après les imports (avant `async def run_extraction`), le helper de normalisation :

```python
def _kind(raw) -> str:
    """Normalise le type d'un nœud : 'value' seulement si explicitement value, sinon entity."""
    return "value" if str(raw or "").strip().lower() == "value" else "entity"
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_pipeline.py -q` → PASS (les 5 tests existants + le nouveau ; les anciens n'émettent pas de `*_kind` → défaut `entity`, inchangés).
- [ ] **Step 5 : Commit**

```bash
git add nerve/pipeline.py tests/test_pipeline.py
git commit -m "feat(i5): pipeline — propage le type entité/valeur du fait à la résolution"
```

---

## Task 7 : web/theme.css + index.html (extraction styles/markup, thèmes clair/sombre)

**Files:** Create `nerve/web/theme.css` ; Modify `nerve/web/index.html` (pas de test auto — suite verte + smoke)

- [ ] **Step 1 : Créer** `nerve/web/theme.css` :

```css
/* nerve/web/theme.css — palette scriptorium, thèmes clair + sombre */
:root{
  --paper:#F4F6FA; --card:#FFFFFF; --ink:#15202E; --ink-soft:#43536A; --ink-faint:#7A889B;
  --blue:#23537F; --blue-deep:#142E49; --blue-bright:#2C77B6; --bordeaux:#7C2A38; --line:#D7DFE9;
}
html[data-theme="dark"]{
  --paper:#0F1A26; --card:#19293A; --ink:#E7EEF6; --ink-soft:#9FB3C8; --ink-faint:#7F93A8;
  --blue:#6FA8DA; --blue-deep:#0A131C; --blue-bright:#2C77B6; --bordeaux:#C2566A; --line:#2A3D52;
}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:var(--paper);color:var(--ink);
  font-family:-apple-system,system-ui,sans-serif}
#top{display:flex;gap:8px;padding:10px;background:var(--blue-deep)}
#top textarea{flex:1;height:46px;border-radius:6px;border:1px solid var(--line);padding:8px;font-family:inherit}
#top button{background:var(--blue-bright);color:#fff;border:0;border-radius:6px;padding:0 16px;cursor:pointer}
#graph{position:absolute;left:260px;right:0;bottom:0;top:66px}
#side{position:absolute;left:0;top:66px;bottom:0;width:260px;overflow:auto;
  background:var(--card);border-right:1px solid var(--line);padding:10px;font-size:13px}
#side h3{margin:10px 0 4px;font-size:12px;text-transform:uppercase;color:var(--blue)}
#side input{width:100%;padding:6px;margin:2px 0;border:1px solid var(--line);border-radius:5px;
  background:var(--card);color:var(--ink)}
#side button{width:100%;padding:6px;margin:2px 0;background:var(--blue-bright);color:#fff;border:0;border-radius:5px;cursor:pointer}
#side .item{padding:4px 6px;border-radius:5px;cursor:pointer}
#side .item:hover{background:var(--paper)}
#results .r{padding:4px 6px;border-bottom:1px solid var(--line);font-size:12px}
#controls{position:absolute;top:74px;left:268px;display:flex;gap:6px;flex-wrap:wrap;align-items:center;z-index:5;font-size:11px}
#controls label{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:3px 7px;color:var(--ink)}
#controls select{border:0;background:transparent;color:var(--blue);font:inherit;cursor:pointer}
#controls .toggle{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:4px 8px;color:var(--ink-soft);cursor:pointer}
#controls .toggle.on{background:var(--blue);color:#fff;border-color:var(--blue)}
#legend{position:absolute;bottom:12px;left:268px;background:var(--card);border:1px solid var(--line);
  border-radius:8px;padding:8px 10px;font-size:10px;color:var(--ink-soft);z-index:5;max-width:220px}
#legend .lt{color:var(--blue);font-size:9px;text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px}
#legend .row{display:flex;align-items:center;gap:6px;margin-top:2px}
#legend .dot{width:10px;height:10px;border-radius:50%;display:inline-block}
#legend .bar{width:16px;height:3px;display:inline-block}
#factcard{position:absolute;display:none;max-width:230px;background:var(--card);border:1px solid var(--line);
  border-radius:9px;padding:9px;box-shadow:0 6px 20px rgba(20,46,73,.22);z-index:8;font-size:12px;color:var(--ink);pointer-events:none}
#factcard .triple b{color:var(--ink)} #factcard .pred{color:var(--blue)}
#factcard .meta{font-size:10px;color:var(--ink-faint);margin-top:5px;display:flex;gap:10px}
```

- [ ] **Step 2 : Remplacer `nerve/web/index.html`** par (markup minimal ; styles → `theme.css` ; script → `graph.js` ; CDN graphology ajoutés) :

```html
<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>nerve</title>
<link rel="stylesheet" href="/theme.css">
<script src="https://unpkg.com/force-graph@1.43.5/dist/force-graph.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/graphology@0.25.4/dist/graphology.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/graphology-library@0.7.0/dist/graphology-library.min.js"></script>
</head>
<body>
  <div id="top">
    <textarea id="txt" placeholder="Colle un texte à transformer en graphe…"></textarea>
    <button id="go">Extraire</button>
  </div>
  <div id="graph"></div>
  <div id="controls">
    <label>Couleur
      <select id="colorMode">
        <option value="community">Communauté</option>
        <option value="set">Set</option>
        <option value="type">Type</option>
        <option value="uniform">Uniforme</option>
      </select>
    </label>
    <label>Taille
      <select id="sizeMode">
        <option value="centrality">Centralité</option>
        <option value="mentions">Mentions</option>
        <option value="fixed">Fixe</option>
      </select>
    </label>
    <button id="edgeLabelsBtn" class="toggle">étiquettes</button>
    <button id="pathBtn" class="toggle">chemin</button>
    <button id="themeBtn" class="toggle">☾</button>
  </div>
  <div id="legend"></div>
  <div id="factcard"></div>
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
  <script src="/graph.js"></script>
</body>
</html>
```

- [ ] **Step 3 : Vérifier la suite** — `uv run pytest -q` → PASS (aucun code Python touché ; compte inchangé).
- [ ] **Step 4 : Commit**

```bash
git add nerve/web/theme.css nerve/web/index.html
git commit -m "feat(i5): front — theme.css (clair/sombre) + index.html (markup + contrôles, JS/CSS externalisés)"
```

---

## Task 8 : web/graph.js — module de visualisation (force-graph + graphology + modes + thème)

**Files:** Create `nerve/web/graph.js` (pas de test auto — suite verte + smoke). Réintègre tout le comportement I-4 (SSE live, navigation sets/docs/recherche/transverse) et ajoute la couche I-5.

- [ ] **Step 1 : Créer** `nerve/web/graph.js` :

```javascript
// nerve/web/graph.js — visualisation nerve (rendu force-graph + analytics graphology)
// NB CDN (à confirmer au smoke) : globals `graphology` (constructeur Graph) et
// `graphologyLibrary` (communitiesLouvain, etc.) exposés par les UMD chargés dans index.html.

const THEMES = {
  light: {bg:"#F4F6FA", node:"#23537F", value:"#7A889B", bridge:"#7C2A38",
          link:"rgba(35,83,127,0.30)", text:"#15202E",
          comm:["#23537F","#1C6A4C","#7C2A38","#2C77B6","#9B3443","#43536A","#B07A1E"]},
  dark:  {bg:"#0F1A26", node:"#2C77B6", value:"#7A889B", bridge:"#C2566A",
          link:"rgba(111,168,218,0.30)", text:"#cfe0f0",
          comm:["#6FA8DA","#3FA77E","#C2566A","#2C77B6","#D98AA0","#9FB3C8","#D6A84E"]},
};
let theme = localStorage.getItem("nerve-theme") || "light";
document.documentElement.setAttribute("data-theme", theme);
const T = () => THEMES[theme];

let colorMode = "community", sizeMode = "centrality", showEdgeLabels = false;
let pathKeys = new Set();   // arêtes "srctgt" surlignées (chemin le plus long)

// force-graph rend les labels en HTML : on échappe le texte LLM (XSS stocké).
function escapeHtml(str){
  return str ? String(str).replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#039;") : "";
}
const linkKey = (l) => (l.source.id||l.source) + "" + (l.target.id||l.target);

// ---- couleur / taille / arêtes ----
function nodeColor(n){
  if(pathKeys.size && n._inPath) return "#D6A84E";
  if(colorMode === "uniform") return T().node;
  if(colorMode === "type") return n.kind === "value" ? T().value : T().node;
  if(colorMode === "set"){
    const s = n.sets || [];
    if(s.length > 1) return T().bridge;          // hub multi-sets
    return s.length ? T().comm[s[0] % T().comm.length] : T().node;
  }
  return T().comm[(n.community || 0) % T().comm.length];   // community
}
function nodeVal(n){
  if(sizeMode === "fixed") return 1;
  if(sizeMode === "mentions") return 1 + (n.mentions || 0);
  return 1 + (n.centrality || 0);              // centralité (degré)
}
function linkColor(l){
  if(pathKeys.has(linkKey(l))) return "#D6A84E";
  return l.is_bridge ? T().bridge : T().link;
}
function linkWidth(l){
  if(pathKeys.has(linkKey(l))) return 4;
  const c = (l.confidence == null ? 70 : l.confidence) / 100;
  return l.is_bridge ? 3 : 0.5 + 2 * c;        // confiance -> épaisseur
}
function drawEdgeLabel(link, ctx, scale){
  if(!showEdgeLabels || !link.predicate) return;
  const s = link.source, t = link.target;
  if(typeof s !== "object" || typeof t !== "object") return;
  const x = (s.x + t.x) / 2, y = (s.y + t.y) / 2;
  const f = 10 / scale;
  ctx.font = `${f}px -apple-system,system-ui,sans-serif`;
  ctx.fillStyle = T().text;
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(link.predicate, x, y);
}

const G = ForceGraph()(document.getElementById("graph"))
  .nodeLabel(n => escapeHtml(n.label || n.id))
  .linkDirectionalArrowLength(3.5).linkDirectionalArrowRelPos(0.92);

function applyStyles(){
  G.backgroundColor(T().bg)
   .nodeColor(nodeColor).nodeVal(nodeVal)
   .linkColor(linkColor).linkWidth(linkWidth)
   .linkCanvasObjectMode(() => showEdgeLabels ? "after" : undefined)
   .linkCanvasObject(drawEdgeLabel);
  renderLegend();
}

// ---- analytics graphology (communautés louvain + centralité de degré) ----
function analyze(data){
  try{
    const g = new graphology.Graph({type:"undirected", allowSelfLoops:true});
    data.nodes.forEach(n => { if(!g.hasNode(n.id)) g.addNode(n.id); });
    data.links.forEach(l => {
      const a = l.source.id || l.source, b = l.target.id || l.target;
      if(a !== b && !g.hasEdge(a, b)) g.addEdge(a, b);
    });
    graphologyLibrary.communitiesLouvain.assign(g);
    data.nodes.forEach(n => {
      n.community = g.getNodeAttribute(n.id, "community") || 0;
      n.centrality = g.degree(n.id);
    });
  }catch(e){ console.warn("graphology indisponible:", e); }
}

// ---- chemin le plus long (heuristique bornée : DFS depuis les hauts degrés) ----
function longestPath(data){
  const adj = new Map(); data.nodes.forEach(n => adj.set(n.id, []));
  data.links.forEach(l => {
    const a = l.source.id || l.source, b = l.target.id || l.target;
    if(adj.has(a) && adj.has(b)){ adj.get(a).push(b); adj.get(b).push(a); }
  });
  const order = [...adj.keys()].sort((x, y) => adj.get(y).length - adj.get(x).length);
  let best = [];
  const CAP = 6;                                  // fanout plafonné (anti-explosion)
  function dfs(node, seen, path){
    if(path.length > best.length) best = path.slice();
    let n = 0;
    for(const nb of adj.get(node)){
      if(seen.has(nb)) continue;
      if(++n > CAP) break;
      seen.add(nb); path.push(nb); dfs(nb, seen, path);
      path.pop(); seen.delete(nb);
    }
  }
  for(const start of order.slice(0, 8)){
    dfs(start, new Set([start]), [start]);
    if(best.length >= adj.size) break;
  }
  const keys = new Set();
  for(let i = 0; i + 1 < best.length; i++)
    keys.add(best[i] + "" + best[i+1]).add(best[i+1] + "" + best[i]);
  data.nodes.forEach(n => { n._inPath = best.includes(n.id); });
  return keys;
}

// ---- rendu d'un graphe {nodes, links} (set / transverse) ----
let nodes = new Map(), links = [], linkKeys = new Set();
function renderGraph(data){
  linkKeys = new Set();
  const d = {nodes: data.nodes || [], links: data.links || []};
  analyze(d);
  pathKeys = document.getElementById("pathBtn").classList.contains("on")
    ? longestPath(d) : new Set();
  G.graphData(d);
  applyStyles();
}

// ---- légende dynamique selon le mode de couleur ----
function legendRow(color, label, bar){
  const row = document.createElement("div"); row.className = "row";
  const mark = document.createElement("span");
  mark.className = bar ? "bar" : "dot"; mark.style.background = color;
  row.appendChild(mark); row.appendChild(document.createTextNode(" " + label));
  return row;
}
function renderLegend(){
  const box = document.getElementById("legend"); box.replaceChildren();
  const title = document.createElement("div"); title.className = "lt";
  const data = G.graphData();
  if(colorMode === "type"){
    title.textContent = "Type"; box.appendChild(title);
    box.appendChild(legendRow(T().node, "entité"));
    box.appendChild(legendRow(T().value, "valeur"));
  }else if(colorMode === "set"){
    title.textContent = "Sets"; box.appendChild(title);
    const sets = [...new Set(data.nodes.flatMap(n => n.sets || []))].sort((a,b)=>a-b);
    sets.forEach(s => box.appendChild(legendRow(T().comm[s % T().comm.length], "set " + s)));
    box.appendChild(legendRow(T().bridge, "hub multi-sets", true));
  }else if(colorMode === "community"){
    title.textContent = "Communautés"; box.appendChild(title);
    const comms = [...new Set(data.nodes.map(n => n.community || 0))].sort((a,b)=>a-b);
    comms.forEach(c => box.appendChild(legendRow(T().comm[c % T().comm.length], "communauté " + c)));
  }else{
    title.textContent = "Uniforme"; box.appendChild(title);
    box.appendChild(legendRow(T().node, "nœud"));
  }
  box.appendChild(legendRow(T().bridge, "passerelle inter-sources", true));
}

// ---- carte de fait au survol d'une arête ----
const card = document.getElementById("factcard");
G.onLinkHover(link => {
  if(!link){ card.style.display = "none"; return; }
  card.replaceChildren();
  const triple = document.createElement("div"); triple.className = "triple";
  const sb = document.createElement("b"); sb.textContent = (link.source.label || link.source.id || "");
  const pr = document.createElement("span"); pr.className = "pred"; pr.textContent = " " + (link.predicate || "") + " ";
  const ob = document.createElement("b"); ob.textContent = (link.target.label || link.target.id || "");
  triple.append(sb, pr, ob);
  const meta = document.createElement("div"); meta.className = "meta";
  const c = document.createElement("span");
  c.textContent = "conf " + (link.confidence == null ? "–" : link.confidence + "%");
  meta.appendChild(c);
  card.append(triple, meta); card.style.display = "block";
});
document.getElementById("graph").addEventListener("mousemove", e => {
  if(card.style.display === "block"){
    card.style.left = (e.offsetX + 14) + "px"; card.style.top = (e.offsetY + 14) + "px";
  }
});

// ---- flux live (extraction SSE) : rendu incrémental, style uniforme pendant le stream ----
function addFact(f){
  const s = f.subject_canonical || f.subject, o = f.object_canonical || f.object;
  if(!s || !o) return;
  nodes.set(s, {id:s}); nodes.set(o, {id:o});
  const k = s + "" + (f.predicate || "") + "" + o;
  if(linkKeys.has(k)) return;
  linkKeys.add(k);
  links.push({source:s, target:o, predicate:f.predicate});
}
function redraw(){ G.graphData({nodes:[...nodes.values()], links}); applyStyles(); }

document.getElementById("go").addEventListener("click", async () => {
  const text = document.getElementById("txt").value.trim(); if(!text) return;
  nodes = new Map(); links = []; linkKeys = new Set(); pathKeys = new Set(); redraw();
  const r = await fetch("/api/documents", {method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({title:"Coller", text})});
  const {document_id} = await r.json();
  const es = new EventSource(`/api/documents/${document_id}/events`);
  es.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if(m.type === "replay"){ m.facts.forEach(addFact); redraw(); }
    else if(m.type === "fact" && !m.is_duplicate){ addFact(m.fact); redraw(); }
    else if(m.type === "done" || m.type === "error"){ es.close(); }
  };
  es.onerror = () => es.close();
});

// ---- navigation sets / docs / recherche / transverse (I-4) ----
async function loadSets(){
  const sets = await (await fetch("/api/sets")).json();
  const box = document.getElementById("sets"); box.replaceChildren();
  sets.forEach(s => {
    const el = document.createElement("div"); el.className = "item";
    el.textContent = `${s.name} (${s.document_count})`;
    el.onclick = () => openSet(s.id, el);
    box.appendChild(el);
  });
}
async function openSet(id, el){
  renderGraph(await (await fetch(`/api/sets/${id}/graph`)).json());
  const detail = await (await fetch(`/api/sets/${id}`)).json();
  const prev = document.querySelector("#setDocs"); if(prev) prev.remove();
  const sub = document.createElement("div"); sub.id = "setDocs";
  detail.documents.forEach(d => {
    const elDoc = document.createElement("div"); elDoc.className = "item"; elDoc.style.paddingLeft = "16px";
    elDoc.textContent = `· ${d.title}`;
    elDoc.onclick = (e) => { e.stopPropagation(); openDocument(d.id); };
    sub.appendChild(elDoc);
  });
  el.insertAdjacentElement("afterend", sub);
}
async function openDocument(id){
  const facts = await (await fetch(`/api/documents/${id}/facts`)).json();
  nodes = new Map(); links = []; linkKeys = new Set(); pathKeys = new Set();
  facts.forEach(addFact); redraw();
}
document.getElementById("searchBtn").addEventListener("click", async () => {
  const q = document.getElementById("q").value.trim(); if(!q) return;
  const res = await (await fetch(`/api/search?q=${encodeURIComponent(q)}`)).json();
  const box = document.getElementById("results"); box.replaceChildren();
  (res.results || []).forEach(r => {
    const el = document.createElement("div"); el.className = "r";
    el.textContent = `${r.subject} · ${r.predicate} · ${r.object}`;
    box.appendChild(el);
  });
});
document.getElementById("transBtn").addEventListener("click", async () => {
  const ent = document.getElementById("ent").value.trim(); if(!ent) return;
  renderGraph(await (await fetch(`/api/transverse?entity=${encodeURIComponent(ent)}`)).json());
});

// ---- contrôles ----
document.getElementById("colorMode").addEventListener("change", (e) => {
  colorMode = e.target.value; applyStyles();
});
document.getElementById("sizeMode").addEventListener("change", (e) => {
  sizeMode = e.target.value; applyStyles();
});
document.getElementById("edgeLabelsBtn").addEventListener("click", (e) => {
  showEdgeLabels = !showEdgeLabels; e.target.classList.toggle("on", showEdgeLabels); applyStyles();
});
document.getElementById("pathBtn").addEventListener("click", (e) => {
  const on = !e.target.classList.contains("on"); e.target.classList.toggle("on", on);
  pathKeys = on ? longestPath(G.graphData()) : new Set();
  if(!on) G.graphData().nodes.forEach(n => { n._inPath = false; });
  applyStyles();
});
document.getElementById("themeBtn").addEventListener("click", (e) => {
  theme = theme === "light" ? "dark" : "light";
  localStorage.setItem("nerve-theme", theme);
  document.documentElement.setAttribute("data-theme", theme);
  e.target.textContent = theme === "light" ? "☾" : "☀";
  applyStyles();
});

document.getElementById("themeBtn").textContent = theme === "light" ? "☾" : "☀";
applyStyles();
loadSets();
```

- [ ] **Step 2 : Vérifier la suite** — `uv run pytest -q` → PASS (aucun code Python touché).
- [ ] **Step 3 : Commit**

```bash
git add nerve/web/graph.js
git commit -m "feat(i5): front — graph.js (graphology louvain/centralité, 4 modes couleur, taille, arêtes, cartes, chemin, thème)"
```

---

## Task 9 : api.py — routes statiques GET /graph.js + /theme.css

**Files:** Modify `nerve/api.py` ; Test `tests/test_api.py`

(Placée après T7/T8 : les fichiers existent, le test passe.)

- [ ] **Step 1 : Test rouge** — ajouter à `tests/test_api.py` :

```python
def test_static_assets_served(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "3")
    import importlib, nerve.api as api
    importlib.reload(api)
    client = TestClient(api.app)
    js = client.get("/graph.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    css = client.get("/theme.css")
    assert css.status_code == 200 and "css" in css.headers["content-type"]
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_api.py::test_static_assets_served -q` → FAIL (404 : routes absentes).

- [ ] **Step 3 : Implémenter** — dans `nerve/api.py`, ajouter juste **avant** la route racine `@app.get("/")` :

```python
@app.get("/graph.js")
def graph_js():
    return FileResponse(os.path.join(WEB, "graph.js"), media_type="application/javascript")

@app.get("/theme.css")
def theme_css():
    return FileResponse(os.path.join(WEB, "theme.css"), media_type="text/css")
```

(`FileResponse` et `os` sont déjà importés ; `WEB` est la constante déjà utilisée par la route `/`.)

- [ ] **Step 4 : Succès** — `uv run pytest -q` → PASS (suite complète).
- [ ] **Step 5 : Commit**

```bash
git add nerve/api.py tests/test_api.py
git commit -m "feat(i5): api — routes statiques GET /graph.js + /theme.css"
```

---

## Vérifications réelles (smoke — Ollama requis)

1. `rm -f data/nerve.db*` (schéma changé : colonne `kind` ; repartir propre).
2. `uv run nerve` → coller ≥2 textes partageant une entité (ex. « Cluny ») et mêlant entités et valeurs (dates/nombres).
3. **Type** : mode Couleur = « Type » → entités vs valeurs distinguées (dates/nombres en gris). Vérifier en base : `SELECT canonical_name, kind FROM entities` cohérent.
4. **Communautés / Centralité** : mode « Communauté » colore par cluster louvain ; taille « Centralité » grossit les nœuds centraux.
5. **Set / Passerelles** : sur un graphe de set ou transverse multi-documents, mode « Set » + hubs multi-sets en bordeaux ; les arêtes incidentes aux entités partagées ressortent (passerelles).
6. **Arêtes** : confiance → épaisseur ; toggle « étiquettes » affiche les prédicats ; survol d'arête → carte de fait.
7. **Chemin** : toggle « chemin » surligne un long chemin (or).
8. **Thème** : toggle ☾/☀ bascule clair/sombre (fond canvas + couleurs re-rendus), persistant au rechargement.
9. **Confirmer les globals graphology** : si la console montre « graphology indisponible », ajuster le nom du global UMD (`graphology` / `graphologyLibrary`) ou l'URL CDN dans `index.html`.

---

## Self-Review (couverture spec → tâches)

- Spec §4 schéma `entities.kind` + migration par re-ingestion → **T1**.
- Spec §5 extraction `subject_kind`/`object_kind` + prompt → **T4**.
- Spec §6 résolution `kind` + conflit entity-domine-value → **T1** (`promote_entity_kind`) + **T5** (resolve) + **T6** (pipeline câble le kind).
- Spec §7 payload enrichi (`kind`, `sets`, `confidence`, `is_bridge`) → **T2** (`_GRAPH_COLS`/JOIN) + **T3** (`build_graph`).
- Spec §8 front (graphology louvain/centralité, modes couleur/taille, confiance/passerelles/étiquettes, cartes de fait, légende, thèmes, chemin) → **T8** ; styles/markup/thèmes → **T7**.
- Spec §9 serving statique `graph.js`/`theme.css` → **T9**.
- Spec §10 tests : backend TDD T1-T6, T9 ; front = suite verte + smoke (T7/T8).

Cohérence des signatures : `create_entity(…, kind)` (T1) appelée par `resolve(…, kind)` (T5) ; `promote_entity_kind` (T1) appelée par resolve (T5) et le faux store du test preload (T5) ; `_kind()` (T6) normalise le champ LLM (T4) ; `_GRAPH_COLS` (T2) produit `s_kind/o_kind/set_id` consommés par `build_graph` (T3) ; `build_graph` produit `kind/sets/confidence/is_bridge` consommés par `graph.js` (`nodeColor`/`nodeVal`/`linkColor`/`linkWidth`, T8) ; routes `/graph.js`/`/theme.css` (T9) servent les fichiers créés en T7/T8.
```
