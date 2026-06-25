# Type de nœud catégoriel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer le type de nœud binaire `entity|value` par un enum de 6 catégories (`personne/lieu/organisation/concept/date/quantite`), résolu par vote majoritaire (intra-document persisté + cross-document au collapse), avec coloration et légende enrichies au front.

**Architecture:** Une taxonomie partagée (`nerve/kinds.py`) centralise les catégories, la normalisation et la règle de victoire (argmax + tie-break par ordre). Le LLM étiquette chaque extrémité de fait ; `entities.kind_votes` (Counter JSON) accumule les votes par entité et `entities.kind` en est l'argmax ; `build_graph` somme les votes des entités de même clé au collapse. Le front colore le mode « Type » sur 6 teintes Okabe-Ito.

**Tech Stack:** Python 3.12 + pytest (backend, `uv run pytest`), SQLite (schéma jetable, pas de migration), JS navigateur (front, vérifié par harness Node / smoke).

**Préalable schéma :** changement de schéma → la DB de dev doit être recréée (`rm -f data/nerve.db*`) avant tout smoke réel. Les tests créent des bases temporaires, sans incidence.

---

### Task 1: Module taxonomie partagé `nerve/kinds.py`

**Files:**
- Create: `nerve/kinds.py`
- Test: `tests/test_kinds.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kinds.py
from nerve.kinds import KINDS, DEFAULT_KIND, normalize_kind, winner

def test_taxonomie_ordonnee_a_six_categories():
    assert KINDS == ["personne", "lieu", "organisation", "concept", "date", "quantite"]
    assert DEFAULT_KIND == "concept"

def test_normalize_kind_accepte_les_categories_et_replie_l_inconnu():
    assert normalize_kind("Personne") == "personne"
    assert normalize_kind("  DATE ") == "date"
    assert normalize_kind("entity") == "concept"   # ancien domaine -> repli
    assert normalize_kind(None) == "concept"

def test_winner_argmax_avec_tie_break_par_ordre():
    assert winner({"personne": 3, "concept": 1}) == "personne"
    # égalité 2-2 : l'ordre de KINDS tranche (lieu avant organisation)
    assert winner({"organisation": 2, "lieu": 2}) == "lieu"
    # tie-break = position dans KINDS : concept (4e) précède quantite (6e) -> gagne
    assert winner({"concept": 2, "quantite": 2}) == "concept"
    assert winner({}) == DEFAULT_KIND
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_kinds.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nerve.kinds'`

- [ ] **Step 3: Write minimal implementation**

```python
# nerve/kinds.py
"""Taxonomie des types de nœud (issue #11). Source unique pour store / pipeline / graph."""

KINDS = ["personne", "lieu", "organisation", "concept", "date", "quantite"]
DEFAULT_KIND = "concept"   # repli pour l'abstrait / l'ambigu / l'inconnu

def normalize_kind(raw) -> str:
    """Ramène une étiquette LLM à une catégorie valide ; repli DEFAULT_KIND sinon."""
    k = str(raw or "").strip().lower()
    return k if k in KINDS else DEFAULT_KIND

def winner(votes: dict) -> str:
    """Catégorie majoritaire d'un Counter de votes ; à égalité, l'ordre de KINDS
    tranche (indice le plus petit). Counter vide -> DEFAULT_KIND. Suppose des clés
    valides (cf. normalize_kind à l'entrée du vote)."""
    if not votes:
        return DEFAULT_KIND
    return min(votes, key=lambda k: (-votes[k], KINDS.index(k)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_kinds.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add nerve/kinds.py tests/test_kinds.py
git commit -m "feat(kinds): module taxonomie partagé (6 catégories + vote) (#11)"
```

---

### Task 2: Schéma + vote persisté dans `store.py`

**Files:**
- Modify: `nerve/store.py` (SCHEMA `entities`, `create_entity`, remplacer `promote_entity_kind`)
- Test: `tests/test_store.py` (remplacer `test_entity_kind_default_and_promote`)

- [ ] **Step 1: Write the failing test** — remplace `test_entity_kind_default_and_promote` (≈ lignes 253-264)

```python
def test_entity_kind_vote_majoritaire(tmp_path):
    st = Store(str(tmp_path / "kind.db"), embed_dim=3); st.init_db()
    d = st.create_document(st.create_set("S"), "d", "text")
    def kind_of(eid):
        return st.conn.execute("SELECT kind FROM entities WHERE id=?", (eid,)).fetchone()[0]
    e = st.create_entity(d, "Cluny", "cluny", kind="organisation")
    assert kind_of(e) == "organisation"                 # 1er vote
    st.vote_entity_kind(e, "lieu")                       # 1-1 : tie-break ordre -> lieu
    assert kind_of(e) == "lieu"
    st.vote_entity_kind(e, "organisation")              # organisation 2, lieu 1
    assert kind_of(e) == "organisation"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_store.py::test_entity_kind_vote_majoritaire -q`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'vote_entity_kind'`

- [ ] **Step 3: Write minimal implementation**

In `nerve/store.py`, add the import near the top (after `import json`):

```python
from nerve.kinds import DEFAULT_KIND, winner
```

In `SCHEMA`, change the `entities` table `kind` line and add `kind_votes`:

```sql
  kind TEXT DEFAULT 'concept',
  kind_votes TEXT DEFAULT '{}',
```

Change `create_entity` to seed the vote (current signature uses `kind: str = "entity"`):

```python
    def create_entity(self, document_id: int, canonical_name: str,
                      normalized_key: str, kind: str = DEFAULT_KIND) -> int:
        cur = self.conn.execute(
            "INSERT INTO entities(document_id, canonical_name, normalized_key, kind, kind_votes) "
            "VALUES (?, ?, ?, ?, ?)",
            (document_id, canonical_name, normalized_key, kind, json.dumps({kind: 1})))
        self.conn.commit()
        return cur.lastrowid
```

Add `vote_entity_kind` **next to** `promote_entity_kind` (do NOT remove `promote_entity_kind` yet — `entities.py` still calls it until Task 5, which removes it):

```python
    def vote_entity_kind(self, entity_id: int, categorie: str) -> None:
        """Ajoute une voix pour `categorie` et recalcule kind = catégorie majoritaire
        (tie-break par ordre de la taxonomie). Fail-loud si l'état est illisible."""
        row = self.conn.execute(
            "SELECT kind_votes FROM entities WHERE id = ?", (entity_id,)).fetchone()
        votes = json.loads(row["kind_votes"])          # lève si absent/illisible (fail-loud)
        votes[categorie] = votes.get(categorie, 0) + 1
        self.conn.execute(
            "UPDATE entities SET kind = ?, kind_votes = ? WHERE id = ?",
            (winner(votes), json.dumps(votes), entity_id))
        self.conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest -q` (full suite — `promote_entity_kind` kept, so `entities`/`pipeline` stay green)
Expected: PASS (old `test_entity_kind_default_and_promote` replaced by the vote test; `promote_entity_kind` still present and used by `entities.py`)

- [ ] **Step 5: Commit**

```bash
git add nerve/store.py tests/test_store.py
git commit -m "feat(store): kind_votes + vote_entity_kind (vote majoritaire) (#11)"
```

---

### Task 3: Schéma d'extraction + prompt dans `extract.py`

**Files:**
- Modify: `nerve/extract.py` (`FACT_SCHEMA`, `SYSTEM_PROMPT`)
- Test: `tests/test_extract.py` (mettre à jour `test_fact_schema_has_kind_fields`)

- [ ] **Step 1: Write the failing test** — remplace l'assertion enum (lignes 36-40)

```python
def test_fact_schema_has_kind_fields():
    from nerve.extract import FACT_SCHEMA
    from nerve.kinds import KINDS
    props = FACT_SCHEMA["properties"]
    assert props["subject_kind"]["enum"] == KINDS
    assert props["object_kind"]["enum"] == KINDS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_extract.py::test_fact_schema_has_kind_fields -q`
Expected: FAIL — `assert ["entity", "value"] == ["personne", ...]`

- [ ] **Step 3: Write minimal implementation**

In `nerve/extract.py`, import the taxonomy at the top (after `import json`):

```python
from nerve.kinds import KINDS
```

Change the two enum lines in `FACT_SCHEMA`:

```python
        "subject_kind": {"type": "string", "enum": KINDS},
        "object_kind": {"type": "string", "enum": KINDS},
```

Replace the `entity|value` paragraph in `SYSTEM_PROMPT` (the block starting "Pour subject ET object, indique aussi son type…" through the `Ex. (Cluny, fonde, 910)…` line) with:

```python
    "Pour subject ET object, indique sa catégorie via subject_kind / object_kind, "
    "parmi : personne (individu nommé), lieu (lieu géographique), organisation "
    "(institution, groupe, entreprise), concept (idée, méthode, œuvre, événement, "
    "abstrait), date (année, siècle, date), quantite (nombre, mesure, durée, "
    "proportion). En cas de doute, utilise « concept ». "
    "Ex. (Cluny, fonde, 910) -> subject_kind=organisation, object_kind=date.\n\n"
```

In the final field-enumeration sentence, change `subject_kind (entity|value)` → `subject_kind (catégorie)` and `object_kind (entity|value)` → `object_kind (catégorie)` (keep "N'omets JAMAIS subject_kind ni object_kind").

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_extract.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add nerve/extract.py tests/test_extract.py
git commit -m "feat(extract): enum 6 catégories + prompt catégoriel (#11)"
```

---

### Task 4: Normalisation dans `pipeline._kind`

**Files:**
- Modify: `nerve/pipeline.py` (`_kind`)
- Test: `tests/test_pipeline.py` (mettre à jour `fake_stream_kinds` + `test_run_extraction_persists_kind`)

- [ ] **Step 1: Write the failing test** — mets à jour le faux flux (ligne ~91-93) et l'assertion (ligne ~124)

```python
async def fake_stream_kinds(cfg, messages, **kw):
    yield ('[{"subject":"Cluny","predicate":"fonde_en","object":"910",'
           '"subject_kind":"organisation","object_kind":"date"}]')
```

et dans `test_run_extraction_persists_kind`, l'assertion finale :

```python
    assert kinds["Cluny"] == "organisation" and kinds["910"] == "date"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pipeline.py::test_run_extraction_persists_kind -q`
Expected: FAIL — `kinds["Cluny"]` vaut `"entity"` (ancien `_kind` mappe tout non-"value" vers "entity")

- [ ] **Step 3: Write minimal implementation**

In `nerve/pipeline.py`, replace the `_kind` function (lines 13-15) with a delegation to the shared taxonomy. Update the import line (line 7 area) to add the helper:

```python
from nerve.kinds import normalize_kind
```

```python
def _kind(raw) -> str:
    """Catégorie normalisée d'un nœud (repli 'concept' si hors taxonomie)."""
    return normalize_kind(raw)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_pipeline.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add nerve/pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): _kind normalise vers la taxonomie catégorielle (#11)"
```

---

### Task 5: Vote à chaque occurrence dans `entities.resolve`

**Files:**
- Modify: `nerve/entities.py` (`resolve`)
- Modify: `nerve/store.py` (supprimer `promote_entity_kind`, devenu code mort)
- Test: `tests/test_entities.py` (remplacer `test_resolver_assigns_and_promotes_kind` + son mock `promote_entity_kind`)

- [ ] **Step 1: Write the failing test** — remplace le test promotion (lignes 57, 65-76)

Dans le faux store du fichier (la classe qui définit `def promote_entity_kind(self, eid): pass`), remplace cette ligne par un vote réel délégué au vrai store si le test utilise un `Store`. Le test utilise un vrai `Store` (`st`), donc remplace le test :

```python
async def test_resolver_vote_le_kind_a_chaque_occurrence(tmp_path):
    async def embed_one(s):
        return [1.0, 0.0, 0.0]                          # vecteur fixe (réidentif. par clé)
    st = Store(str(tmp_path / "r.db"), embed_dim=3); st.init_db()
    d = st.create_document(st.create_set("S"), "doc", "text")
    r = EntityResolver(st, d, embed_one, threshold=0.9)
    def kind_of(eid):
        return st.conn.execute("SELECT kind FROM entities WHERE id=?", (eid,)).fetchone()[0]
    a = await r.resolve("Cluny", kind="organisation")
    assert kind_of(a) == "organisation"
    b = await r.resolve("Cluny", kind="lieu")          # même clé : 1-1, tie-break -> lieu
    assert b == a and kind_of(a) == "lieu"
    await r.resolve("Cluny", kind="organisation")      # organisation 2, lieu 1
    assert kind_of(a) == "organisation"
```

(Le test est autonome : `EntityResolver` et `Store` sont déjà importés en tête de `tests/test_entities.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_entities.py::test_resolver_vote_le_kind_a_chaque_occurrence -q`
Expected: FAIL — `resolve` n'appelle pas `vote_entity_kind` ; `kind_of(a)` reste `"organisation"` après le 2e appel.

- [ ] **Step 3: Write minimal implementation**

In `nerve/entities.py` `resolve`, replace the two promotion blocks. Currently:

```python
            if kind == "entity":
                self.store.promote_entity_kind(eid)
```

appears twice (clé déjà connue, et match embedding). Replace **both** with an unconditional vote:

```python
            self.store.vote_entity_kind(eid, kind)
```

(La création initiale via `create_entity` sème déjà le premier vote ; ne pas re-voter dans la branche `eid is None`.)

Puis, dans `nerve/store.py`, **supprime** la méthode `promote_entity_kind` (plus aucun appelant). Vérifie d'abord qu'il ne reste aucune référence : `grep -rn promote_entity_kind nerve/ tests/` doit ne plus rien renvoyer après l'édition.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_entities.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add nerve/entities.py tests/test_entities.py
git commit -m "feat(entities): resolve vote la catégorie à chaque occurrence (#11)"
```

---

### Task 6: Exposer votes + entity_id dans `_GRAPH_COLS`

**Files:**
- Modify: `nerve/store.py` (`_GRAPH_COLS`)
- Test: `tests/test_store.py` (mettre à jour `test_graph_cols_expose_kind_and_set`)

- [ ] **Step 1: Write the failing test** — étends l'assertion (lignes 240-251)

```python
def test_graph_cols_expose_kind_votes_et_entity_id(tmp_path):
    st = Store(str(tmp_path / "gc.db"), embed_dim=3); st.init_db()
    s = st.create_set("S"); d = st.create_document(s, "doc", "text")
    se = st.create_entity(d, "Cluny", "cluny", kind="organisation")
    oe = st.create_entity(d, "910", "910", kind="date")
    st.add_fact(d, {"subject": "Cluny", "predicate": "fonde_en", "object": "910",
                    "confidence": 80}, subject_entity_id=se, object_entity_id=oe)
    r0 = st.facts_for_set(s)[0]
    assert r0["s_kind"] == "organisation" and r0["o_kind"] == "date"
    assert r0["s_entity_id"] == se and r0["o_entity_id"] == oe
    assert r0["s_votes"] == '{"organisation": 1}' and r0["o_votes"] == '{"date": 1}'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_store.py::test_graph_cols_expose_kind_votes_et_entity_id -q`
Expected: FAIL — `KeyError: 's_entity_id'`

- [ ] **Step 3: Write minimal implementation**

In `nerve/store.py`, extend `_GRAPH_COLS` (currently ends with `oe.mention_count AS o_mentions, oe.kind AS o_kind`):

```python
    _GRAPH_COLS = (
        "f.id AS fact_id, f.predicate AS predicate, f.confidence AS confidence, "
        "f.document_id AS document_id, d.set_id AS set_id, "
        "se.id AS s_entity_id, oe.id AS o_entity_id, "
        "se.kind_votes AS s_votes, oe.kind_votes AS o_votes, "
        "se.normalized_key AS s_key, se.canonical_name AS s_name, "
        "se.mention_count AS s_mentions, se.kind AS s_kind, "
        "oe.normalized_key AS o_key, oe.canonical_name AS o_name, "
        "oe.mention_count AS o_mentions, oe.kind AS o_kind")
```

Note (#9) : ces colonnes alimentent `build_graph`, qui renvoie `{nodes, links}` ; `s_entity_id`/`o_entity_id` ne sont jamais propagés au client.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_store.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add nerve/store.py tests/test_store.py
git commit -m "feat(store): _GRAPH_COLS expose kind_votes + entity_id (interne) (#11)"
```

---

### Task 7: Vote majoritaire au collapse dans `build_graph`

**Files:**
- Modify: `nerve/graph.py` (`_add_node`, `build_graph`)
- Test: `tests/test_graph.py` (remplacer `_krow` defaults + les 2 tests `dominates_value`)

- [ ] **Step 1: Write the failing test** — adapte le helper `_krow` et remplace les tests kind (lignes 35-50)

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_graph.py -q`
Expected: FAIL — `_add_node` applique encore « entity domine value » ; `kind` ne reflète pas le vote.

- [ ] **Step 3: Write minimal implementation**

In `nerve/graph.py`, add the import at the top:

```python
import json
from collections import Counter
from nerve.kinds import winner
```

Replace `_add_node` and `build_graph` so the node accumulates per-entity votes (deduped by `entity_id`) and resolves `kind` at the end:

```python
def _add_node(nodes: dict, key: str, name: str, mentions: int,
              entity_id, votes_json, set_id) -> None:
    n = nodes.get(key)
    if n is None:
        n = {"id": key, "label": name or key, "mentions": mentions or 0,
             "_votes": Counter(), "_seen": set(), "_sets": set()}
        nodes[key] = n
    elif (mentions or 0) > n["mentions"]:
        n["label"] = name or key
        n["mentions"] = mentions or 0
    if entity_id is not None and entity_id not in n["_seen"]:   # dédup par entité-doc
        n["_seen"].add(entity_id)
        n["_votes"].update(json.loads(votes_json or "{}"))
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
                  r.get("s_entity_id"), r.get("s_votes"), r.get("set_id"))
        _add_node(nodes, o_key, r.get("o_name"), r.get("o_mentions"),
                  r.get("o_entity_id"), r.get("o_votes"), r.get("set_id"))
        lk = (s_key, r.get("predicate"), o_key)
        if lk not in links:
            links[lk] = {"source": s_key, "target": o_key,
                         "predicate": r.get("predicate"), "fact_id": r.get("fact_id"),
                         "confidence": r.get("confidence")}
    for n in nodes.values():
        n["kind"] = winner(dict(n.pop("_votes")))
        n["sets"] = sorted(n.pop("_sets"))
        n.pop("_seen")
    multi = {key for key, n in nodes.items() if len(n["sets"]) > 1}
    for lk in links.values():
        lk["is_bridge"] = lk["source"] in multi or lk["target"] in multi
    return {"nodes": list(nodes.values()), "links": list(links.values())}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_graph.py -q && uv run pytest -q`
Expected: PASS (toute la suite verte)

- [ ] **Step 5: Commit**

```bash
git add nerve/graph.py tests/test_graph.py
git commit -m "feat(graph): vote majoritaire des catégories au collapse cross-document (#11)"
```

---

### Task 8: Coloration & légende catégorielles au front

**Files:**
- Modify: `nerve/web/graph.js` (`nodeColor`, `renderLegend`, + helpers couleur/libellés)
- Verify: harness Node jetable (mapping) + smoke navigateur optionnel

- [ ] **Step 1: Write the verification harness** (le front n'a pas de tests unitaires ; on vérifie le mapping en isolation)

```javascript
// scratchpad/verif_cat.js — copie des helpers ajoutés à graph.js
const CAT_ORDER = ["personne","lieu","organisation","concept","date","quantite"];
const CAT_LABELS = {personne:"Personne", lieu:"Lieu", organisation:"Organisation",
                    concept:"Concept", date:"Date", quantite:"Quantité"};
const comm = ["#0072B2","#009E73","#E69F00","#CC79A7","#56B4E9","#F0E442"];
const catColor = (kind, node) => { const i = CAT_ORDER.indexOf(kind); return i < 0 ? node : comm[i % comm.length]; };

console.log("6 couleurs distinctes :", new Set(CAT_ORDER.map(k => catColor(k, "#000"))).size === 6);
console.log("inconnu -> couleur node :", catColor("entity", "#NODE") === "#NODE");
console.log("labels FR complets :", CAT_ORDER.every(k => CAT_LABELS[k]));
```

- [ ] **Step 2: Run the harness to verify the mapping**

Run: `node scratchpad/verif_cat.js`
Expected: trois lignes `true`.

- [ ] **Step 3: Implement in `nerve/web/graph.js`**

Après la définition de `cc` (ligne ~24), ajoute les helpers catégoriels :

```javascript
// catégories de nœud (#11) : mapping fixe catégorie -> teinte de la palette active
const CAT_ORDER = ["personne","lieu","organisation","concept","date","quantite"];
const CAT_LABELS = {personne:"Personne", lieu:"Lieu", organisation:"Organisation",
                    concept:"Concept", date:"Date", quantite:"Quantité"};
const catColor = (kind) => {
  const i = CAT_ORDER.indexOf(kind);
  return i < 0 ? cc().node : cc().comm[i % cc().comm.length];
};
```

Dans `nodeColor`, remplace la branche `type` (ligne ~86) :

```javascript
  if(colorMode === "type") return catColor(n.kind);
```

Dans `renderLegend`, remplace le bloc du mode Type (les 2 lignes `legendRow(cc().node,"entité")` / `legendRow(cc().value,"valeur")`, ≈ lignes 228-230) :

```javascript
    title.textContent = "Type"; box.appendChild(title);
    CAT_ORDER.forEach(k => box.appendChild(legendRow(catColor(k), CAT_LABELS[k])));
```

- [ ] **Step 4: Verify syntax + suite backend**

Run: `node --check nerve/web/graph.js && uv run pytest -q`
Expected: `graph.js` OK ; suite backend toujours verte (front non couvert par pytest).

- [ ] **Step 5: Commit**

```bash
git add nerve/web/graph.js
git commit -m "feat(web): coloration et légende catégorielles en mode Type (#11)"
```

---

### Task 9: Smoke réel + vérification de bout en bout

**Files:** aucun (validation manuelle / re-ingestion)

- [ ] **Step 1: Recréer la DB de dev (changement de schéma)**

```bash
rm -f data/nerve.db*
```

- [ ] **Step 2: Lancer l'app et ré-ingérer un document**

Run: `uv run nerve` → http://127.0.0.1:3000 ; extraire un document riche (personnes, lieux, dates, nombres).

- [ ] **Step 3: Vérifier visuellement**

Mode couleur « Type » → 6 catégories distinctes + légende à 6 lignes ; un nœud vu sous des catégories mêlées prend bien la majoritaire (vérifier en base : `kind` vs `kind_votes`).

- [ ] **Step 4: Suite complète**

Run: `uv run pytest -q`
Expected: PASS (toute la suite).

- [ ] **Step 5: Ouvrir la PR**

```bash
git push -u origin feat-11-type-noeud-categoriel
gh pr create --base main --title "feat: type de nœud catégoriel (6 catégories, vote majoritaire) (#11)" --body "Ferme #11. Voir docs/superpowers/specs/2026-06-25-nerve-type-noeud-categoriel-design.md"
```
