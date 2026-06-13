# nerve — Plan 2 / I-1 : qualité du graphe (fusion des nœuds + dedup de faits)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rendre le graphe propre — les variantes d'une entité fusionnent en un nœud, les faits redondants sont dédupliqués — en portant la logique de l'original (prompt canonique + dedup par embedding) et en ajoutant une résolution d'entités (clé lexicale + garde hybride embedding/lexical), le tout adossé à `sqlite-vec`.

**Architecture:** On ajoute trois modules (`embeddings.py`, `entities.py`, `dedup.py`) et on étend `extract`/`store`/`pipeline`/`config`/`api`/`web`. Les working sets de dedup et de résolution d'entités sont **en mémoire par document** (cosinus `numpy`/produit scalaire) ; en parallèle on remplit `vec_facts`/`vec_entities` pour I-4. Embeddings via un provider OpenAI-compatible séparé (Ollama `bge-m3`). Tout est testé sans réseau (embedding factice déterministe + `httpx.MockTransport`), avec un smoke réel final.

**Tech Stack:** Python 3.11+, uv, FastAPI, httpx, sqlite3 + sqlite-vec, numpy, pytest + pytest-asyncio. Embeddings : Ollama `bge-m3` (1024 dim) via API OpenAI-compatible.

**Référence (à relire avant de coder)** : `../knowledge-graph-extractor/app.py` — `embed_fact` (l.87), `check_duplicate` (l.104), `DEFAULT_PROMPT` (l.122). Spec : `docs/superpowers/specs/2026-06-13-nerve-I1-graph-quality-design.md`.

---

## Périmètre & fichiers

```
nerve/
  config.py        # MODIF : entity_threshold, dedup_threshold, dedup_field
  embeddings.py    # CREATE : client /v1/embeddings (httpx), vecteurs L2-normalisés
  extract.py       # MODIF : SYSTEM_PROMPT de canonicalisation fort (remplace celui du Plan 1)
  store.py         # MODIF : schéma (entities, vec_entities, colonnes), CRUD entités, vecteurs, compteurs
  entities.py      # CREATE : normalized_key + lexical_guard + EntityResolver (résolution d'entités)
  dedup.py         # CREATE : dedup_text + FactDeduper (dedup de faits)
  pipeline.py      # MODIF : intègre résolution d'entités + dedup + persistance vectorielle
  api.py           # MODIF : remonte total/unique/duplicate ; n'expose que les faits non-dup (noms canoniques)
  web/index.html   # MODIF : nœuds par nom canonique
tests/
  test_config.py   # MODIF
  test_embeddings.py   # CREATE
  test_extract.py  # MODIF (le prompt porte les règles canoniques)
  test_store.py    # MODIF
  test_entities.py # CREATE
  test_dedup.py    # CREATE
  test_pipeline.py # MODIF
  test_api.py      # MODIF
```

**Conventions d'interface (verrouillées ici, réutilisées partout) :**
- `embed(cfg: ProviderConfig, texts: list[str], *, client=None) -> list[list[float]]` — vecteurs **normalisés**.
- `embed_fn` injecté dans les classes : `async (text: str) -> list[float]` (vrai bge-m3 en prod ; factice en test).
- `EntityResolver(store, document_id, embed_fn, threshold)` → `await resolve(name) -> entity_id (int)`.
- `FactDeduper(embed_fn, threshold, field="triple")` → `await check(fact) -> (is_dup: bool, dup_of_id: int|None, vec: list[float])` ; `add(fact_id, vec)`.
- `store.add_fact(document_id, fact, *, is_duplicate=False, dup_of_id=None, subject_entity_id=None, object_entity_id=None, source_file="") -> int` (rétro-compatible Plan 1).

---

### Task 1 : `config.py` — seuils + champ de dedup

**Files:**
- Modify: `nerve/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1 : Ajouter le test** (append à la fin de `tests/test_config.py`)

```python
def test_i1_defaults(monkeypatch):
    for k in ("ENTITY_THRESHOLD", "DEDUP_THRESHOLD", "DEDUP_FIELD"):
        monkeypatch.delenv(k, raising=False)
    c = cfgmod.load_config()
    assert c.entity_threshold == 0.80
    assert c.dedup_threshold == 0.85
    assert c.dedup_field == "triple"

def test_i1_overrides(monkeypatch):
    monkeypatch.setenv("ENTITY_THRESHOLD", "0.7")
    monkeypatch.setenv("DEDUP_THRESHOLD", "0.9")
    monkeypatch.setenv("DEDUP_FIELD", "title")
    c = cfgmod.load_config()
    assert c.entity_threshold == 0.7
    assert c.dedup_threshold == 0.9
    assert c.dedup_field == "title"
```

- [ ] **Step 2 : Lancer (échec attendu)** — `uv run pytest tests/test_config.py -q` → FAIL (`AttributeError`).

- [ ] **Step 3 : Implémenter** — dans `nerve/config.py`, ajouter trois champs au dataclass `Config` (après `port`) :

```python
    port: int
    entity_threshold: float
    dedup_threshold: float
    dedup_field: str
```

et dans `load_config()`, compléter la construction de `Config(...)` :

```python
    return Config(
        llm=llm, embed=embed,
        embed_dim=int(os.environ.get("EMBED_DIM", "1024")),
        data_dir=data_dir,
        db_path=os.path.join(data_dir, "nerve.db"),
        port=int(os.environ.get("NERVE_PORT", "3000")),
        entity_threshold=float(os.environ.get("ENTITY_THRESHOLD", "0.80")),
        dedup_threshold=float(os.environ.get("DEDUP_THRESHOLD", "0.85")),
        dedup_field=os.environ.get("DEDUP_FIELD", "triple"),
    )
```

- [ ] **Step 4 : Lancer (succès)** — `uv run pytest tests/test_config.py -q` → 4 passed.

- [ ] **Step 5 : Commit**

```bash
git add nerve/config.py tests/test_config.py
git commit -m "feat(i1): seuils de fusion/dedup + dedup_field configurables"
```

---

### Task 2 : `embeddings.py` — client `/v1/embeddings`

**Files:**
- Create: `nerve/embeddings.py`
- Test: `tests/test_embeddings.py`

- [ ] **Step 1 : Écrire le test** (`httpx.MockTransport`, vecteurs renvoyés normalisés)

```python
# tests/test_embeddings.py
import math
import httpx
from nerve.config import ProviderConfig
from nerve.embeddings import embed

async def test_embed_returns_normalized_vectors():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/embeddings")
        body = request.read().decode()
        assert "deux textes" in body or "un texte" in body
        return httpx.Response(200, json={"data": [
            {"embedding": [3.0, 4.0]},     # norme 5 -> (0.6, 0.8)
            {"embedding": [0.0, 2.0]},     # norme 2 -> (0.0, 1.0)
        ]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = ProviderConfig("http://x/v1", "k", "m")
    out = await embed(cfg, ["un texte", "deux textes"], client=client)
    assert len(out) == 2
    assert abs(out[0][0] - 0.6) < 1e-6 and abs(out[0][1] - 0.8) < 1e-6
    assert abs(math.sqrt(sum(v * v for v in out[1])) - 1.0) < 1e-6
```

- [ ] **Step 2 : Lancer (échec)** — `uv run pytest tests/test_embeddings.py -q` → FAIL (`ModuleNotFoundError`).

- [ ] **Step 3 : Implémenter `nerve/embeddings.py`**

```python
# nerve/embeddings.py
import math
import httpx
from nerve.config import ProviderConfig

def _l2_normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]

async def embed(cfg: ProviderConfig, texts: list[str], *,
                client: httpx.AsyncClient | None = None) -> list[list[float]]:
    """Embeddings via un endpoint /embeddings OpenAI-compatible (Ollama bge-m3...).
    Renvoie des vecteurs L2-normalisés (cosinus = produit scalaire)."""
    payload = {"model": cfg.model, "input": texts}
    headers = {"Authorization": f"Bearer {cfg.api_key}"}
    url = f"{cfg.base_url.rstrip('/')}/embeddings"
    owns = client is None
    client = client or httpx.AsyncClient(timeout=None)
    try:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return [_l2_normalize(row["embedding"]) for row in data["data"]]
    finally:
        if owns:
            await client.aclose()
```

- [ ] **Step 4 : Lancer (succès)** — `uv run pytest tests/test_embeddings.py -q` → passed.

- [ ] **Step 5 : Commit**

```bash
git add nerve/embeddings.py tests/test_embeddings.py
git commit -m "feat(i1): client embeddings OpenAI-compatible (httpx, normalisé)"
```

---

### Task 3 : `extract.py` — prompt de canonicalisation fort

**Files:**
- Modify: `nerve/extract.py` (la constante `SYSTEM_PROMPT`)
- Test: `tests/test_extract.py`

Le prompt du Plan 1 est trop faible (d'où `Cluny_Abbey`). On porte les règles canoniques de l'original (en français). Aucune autre partie d'`extract.py` ne change.

- [ ] **Step 1 : Ajouter un test qui verrouille l'intention** (append à `tests/test_extract.py`)

```python
def test_system_prompt_porte_les_regles_canoniques():
    from nerve.extract import SYSTEM_PROMPT
    p = SYSTEM_PROMPT.lower()
    # règles clés : nœuds = entités canoniques, réutiliser la MÊME chaîne, pas de prose
    assert "canonique" in p
    assert "même chaîne" in p or "meme chaine" in p
    assert "description" in p
```

- [ ] **Step 2 : Lancer (échec attendu)** — `uv run pytest tests/test_extract.py -q` → FAIL sur le nouveau test (l'ancien prompt ne contient pas « même chaîne »).

- [ ] **Step 3 : Remplacer `SYSTEM_PROMPT`** dans `nerve/extract.py` par :

```python
SYSTEM_PROMPT = (
    "Tu extrais un graphe de connaissances d'un document. Réponds UNIQUEMENT par un "
    "tableau JSON d'objets (faits atomiques), sans texte autour. Un document dense "
    "justifie 8 à 15 faits ; une page courte 0 à 3.\n\n"
    "RÈGLE LA PLUS IMPORTANTE (elle pilote la connectivité du graphe) : subject et "
    "object DOIVENT être des ENTITÉS canoniques ou des VALEURS atomiques courtes, "
    "jamais de la prose. Ce sont des nœuds : la même entité doit sortir IDENTIQUE à "
    "chaque fois pour que les arêtes se connectent. Mets le récit, la preuve et la "
    "nuance dans description, PAS dans subject/object.\n\n"
    "Règles subject / object :\n"
    "- Utilise le nom canonique le plus court d'une entité réelle (personne, "
    "organisation, lieu, œuvre, méthode, date, nombre+unité, version).\n"
    "- Retire articles, rôles et qualificatifs : « l'équipe de Cluny » -> « Cluny ».\n"
    "- Réutilise EXACTEMENT la même chaîne pour la même entité dans tous les faits "
    "(c'est ainsi que les nœuds fusionnent). Pas de pronom ni de paraphrase.\n"
    "- Jamais de phrase ou de proposition dans subject/object. Si la valeur est "
    "descriptive, mets la valeur atomique dans object et explique dans description.\n"
    "- Privilégie les arêtes entité-entité (deux entités nommées) ; entité-valeur "
    "est correct aussi.\n\n"
    "Pour chaque fait : title (une phrase <=140 car.), description (2-3 phrases "
    "<=350 car. portant la réponse + preuve, citation verbatim si utile), subject, "
    "predicate (relation snake_case précise, <=32 car.), object, evidence_span "
    "(citation verbatim, sous-chaîne du document), confidence (0-100), tags "
    "(minuscules, alphanumérique+tiret)."
)
```

- [ ] **Step 4 : Lancer (succès)** — `uv run pytest tests/test_extract.py -q` → passed (l'ancien test `test_build_messages_includes_text` reste vert car `build_messages` est inchangé).

- [ ] **Step 5 : Commit**

```bash
git add nerve/extract.py tests/test_extract.py
git commit -m "feat(i1): prompt de canonicalisation fort (porté de l'original)"
```

---

### Task 4 : `store.py` — schéma (entités, vecteurs) + CRUD entités

**Files:**
- Modify: `nerve/store.py` (la constante `SCHEMA`, la création des tables virtuelles, nouvelles méthodes)
- Test: `tests/test_store.py`

- [ ] **Step 1 : Ajouter les tests** (append à `tests/test_store.py`)

```python
def test_entities_crud_and_vectors(tmp_path):
    st = Store(str(tmp_path / "e.db"), embed_dim=4)
    st.init_db()
    set_id = st.create_set("S"); doc_id = st.create_document(set_id, "d", "text")
    eid = st.create_entity(doc_id, canonical_name="Cluny", normalized_key="cluny")
    assert isinstance(eid, int)
    assert st.find_entity_by_key(doc_id, "cluny") == eid
    assert st.find_entity_by_key(doc_id, "absent") is None
    st.add_entity_vector(eid, [1.0, 0.0, 0.0, 0.0])      # dim 4
    st.bump_entity_mention(eid)
    st.set_entity_canonical(eid, "Abbaye de Cluny")
    # vec_entities a bien 1 ligne
    n = st.conn.execute("SELECT count(*) FROM vec_entities").fetchone()[0]
    assert n == 1

def test_vec_facts_is_populated(tmp_path):
    st = Store(str(tmp_path / "vf.db"), embed_dim=4)
    st.init_db()
    set_id = st.create_set("S"); doc_id = st.create_document(set_id, "d", "text")
    fid = st.add_fact(doc_id, {"subject": "A", "predicate": "r", "object": "B"})
    st.add_fact_vector(fid, [0.0, 1.0, 0.0, 0.0])
    n = st.conn.execute("SELECT count(*) FROM vec_facts").fetchone()[0]
    assert n == 1
```

- [ ] **Step 2 : Lancer (échec)** — `uv run pytest tests/test_store.py -q` → FAIL (`AttributeError` / table absente).

- [ ] **Step 3 : Modifier `nerve/store.py`.**

(a) Étendre `SCHEMA` : ajouter les colonnes aux `documents` et `facts`, et la table `entities`. Remplace le bloc `SCHEMA = """ ... """` par :

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS source_sets (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  set_id INTEGER REFERENCES source_sets(id),
  title TEXT, source_kind TEXT, source_ref TEXT,
  status TEXT DEFAULT 'running', params_json TEXT,
  total_facts INTEGER DEFAULT 0,
  unique_facts INTEGER DEFAULT 0,
  duplicate_facts INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now')), finished_at TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY,
  document_id INTEGER REFERENCES documents(id),
  subject TEXT, predicate TEXT, object TEXT,
  title TEXT, description TEXT, evidence_span TEXT,
  confidence INTEGER, tags_json TEXT, source_file TEXT,
  is_duplicate INTEGER DEFAULT 0, dup_of_id INTEGER,
  subject_entity_id INTEGER, object_entity_id INTEGER,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS entities (
  id INTEGER PRIMARY KEY,
  document_id INTEGER REFERENCES documents(id),
  canonical_name TEXT NOT NULL, normalized_key TEXT NOT NULL,
  mention_count INTEGER DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_facts_doc ON facts(document_id);
CREATE INDEX IF NOT EXISTS idx_entities_doc_key ON entities(document_id, normalized_key);
"""
```

(b) Dans `init_db()`, après la création de `vec_facts`, ajouter la création de `vec_entities` (juste avant `con.commit()`):

```python
        con.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_entities "
            f"USING vec0(entity_id integer primary key, embedding float[{self.embed_dim}])"
        )
```

(c) Ajouter `import sqlite_vec` est déjà présent. Ajouter ces méthodes à la classe `Store` :

```python
    def create_entity(self, document_id: int, canonical_name: str,
                      normalized_key: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO entities(document_id, canonical_name, normalized_key) "
            "VALUES (?, ?, ?)", (document_id, canonical_name, normalized_key))
        self.conn.commit()
        return cur.lastrowid

    def find_entity_by_key(self, document_id: int, normalized_key: str) -> int | None:
        r = self.conn.execute(
            "SELECT id FROM entities WHERE document_id = ? AND normalized_key = ?",
            (document_id, normalized_key)).fetchone()
        return r["id"] if r else None

    def set_entity_canonical(self, entity_id: int, canonical_name: str) -> None:
        self.conn.execute("UPDATE entities SET canonical_name = ? WHERE id = ?",
                          (canonical_name, entity_id))
        self.conn.commit()

    def bump_entity_mention(self, entity_id: int) -> None:
        self.conn.execute(
            "UPDATE entities SET mention_count = mention_count + 1 WHERE id = ?",
            (entity_id,))
        self.conn.commit()

    def add_entity_vector(self, entity_id: int, embedding: list[float]) -> None:
        self.conn.execute(
            "INSERT INTO vec_entities(entity_id, embedding) VALUES (?, ?)",
            (entity_id, sqlite_vec.serialize_float32(embedding)))
        self.conn.commit()

    def add_fact_vector(self, fact_id: int, embedding: list[float]) -> None:
        self.conn.execute(
            "INSERT INTO vec_facts(fact_id, embedding) VALUES (?, ?)",
            (fact_id, sqlite_vec.serialize_float32(embedding)))
        self.conn.commit()
```

- [ ] **Step 4 : Lancer (succès)** — `uv run pytest tests/test_store.py -q` → passed (les tests Plan 1 restent verts : colonnes ajoutées avec défauts, `add_fact` inchangé pour l'instant).

- [ ] **Step 5 : Commit**

```bash
git add nerve/store.py tests/test_store.py
git commit -m "feat(i1): schéma entités + vec_entities + persistance vectorielle"
```

---

### Task 5 : `store.py` — `add_fact` étendu (entités, dup, compteurs) + `get_facts` non-dup canonique

**Files:**
- Modify: `nerve/store.py` (`add_fact`, `get_facts`)
- Test: `tests/test_store.py`

- [ ] **Step 1 : Ajouter les tests** (append à `tests/test_store.py`)

```python
def test_add_fact_counts_and_entities(tmp_path):
    st = Store(str(tmp_path / "c.db"), embed_dim=4)
    st.init_db()
    set_id = st.create_set("S"); doc_id = st.create_document(set_id, "d", "text")
    e1 = st.create_entity(doc_id, "A", "a"); e2 = st.create_entity(doc_id, "B", "b")
    st.add_fact(doc_id, {"subject": "A", "predicate": "r", "object": "B"},
                subject_entity_id=e1, object_entity_id=e2)
    st.add_fact(doc_id, {"subject": "A", "predicate": "r", "object": "B"},
                is_duplicate=True, dup_of_id=1)
    doc = st.get_document(doc_id)
    assert doc["total_facts"] == 2
    assert doc["unique_facts"] == 1
    assert doc["duplicate_facts"] == 1
    facts = st.get_facts(doc_id)                 # par défaut : non-dup seulement
    assert len(facts) == 1
    assert facts[0]["subject_canonical"] == "A"  # nom canonique via l'entité

def test_get_facts_can_include_duplicates(tmp_path):
    st = Store(str(tmp_path / "d.db"), embed_dim=4)
    st.init_db()
    set_id = st.create_set("S"); doc_id = st.create_document(set_id, "d", "text")
    st.add_fact(doc_id, {"subject": "A", "predicate": "r", "object": "B"})
    st.add_fact(doc_id, {"subject": "A", "predicate": "r", "object": "B"},
                is_duplicate=True, dup_of_id=1)
    assert len(st.get_facts(doc_id)) == 1
    assert len(st.get_facts(doc_id, include_duplicates=True)) == 2
```

- [ ] **Step 2 : Lancer (échec)** — `uv run pytest tests/test_store.py -q` → FAIL (signature `add_fact` / clé `subject_canonical`).

- [ ] **Step 3 : Remplacer `add_fact` et `get_facts`** dans `nerve/store.py` :

```python
    def add_fact(self, document_id: int, fact: dict, *, is_duplicate: bool = False,
                 dup_of_id: int | None = None, subject_entity_id: int | None = None,
                 object_entity_id: int | None = None, source_file: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO facts(document_id, subject, predicate, object, title, "
            "description, evidence_span, confidence, tags_json, source_file, "
            "is_duplicate, dup_of_id, subject_entity_id, object_entity_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (document_id, fact.get("subject"), fact.get("predicate"),
             fact.get("object"), fact.get("title"), fact.get("description"),
             fact.get("evidence_span"), fact.get("confidence"),
             json.dumps(fact.get("tags", [])), source_file,
             1 if is_duplicate else 0, dup_of_id,
             subject_entity_id, object_entity_id))
        if is_duplicate:
            self.conn.execute(
                "UPDATE documents SET total_facts = total_facts + 1, "
                "duplicate_facts = duplicate_facts + 1 WHERE id = ?", (document_id,))
        else:
            self.conn.execute(
                "UPDATE documents SET total_facts = total_facts + 1, "
                "unique_facts = unique_facts + 1 WHERE id = ?", (document_id,))
        self.conn.commit()
        return cur.lastrowid

    def get_facts(self, document_id: int, include_duplicates: bool = False) -> list[dict]:
        where = "" if include_duplicates else " AND f.is_duplicate = 0"
        rows = self.conn.execute(
            "SELECT f.*, se.canonical_name AS subject_canonical, "
            "oe.canonical_name AS object_canonical FROM facts f "
            "LEFT JOIN entities se ON se.id = f.subject_entity_id "
            "LEFT JOIN entities oe ON oe.id = f.object_entity_id "
            "WHERE f.document_id = ?" + where + " ORDER BY f.id",
            (document_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["tags"] = json.loads(d.pop("tags_json") or "[]")
            d["subject_canonical"] = d.get("subject_canonical") or d["subject"]
            d["object_canonical"] = d.get("object_canonical") or d["object"]
            out.append(d)
        return out
```

- [ ] **Step 4 : Lancer (succès)** — `uv run pytest tests/test_store.py -q` → passed (le test Plan 1 `test_create_and_read_facts` reste vert : 2 faits non-dup, `subject` toujours "A", `tags` toujours `["x"]`).

- [ ] **Step 5 : Commit**

```bash
git add nerve/store.py tests/test_store.py
git commit -m "feat(i1): add_fact (entités/dup/compteurs) + get_facts non-dup canonique"
```

---

### Task 6 : `entities.py` — clé lexicale, garde hybride, EntityResolver

**Files:**
- Create: `nerve/entities.py`
- Test: `tests/test_entities.py`

- [ ] **Step 1 : Écrire le test** (embedding factice déterministe ; vrai `Store` en tmp)

```python
# tests/test_entities.py
from nerve.store import Store
from nerve.entities import normalized_key, lexical_guard, EntityResolver

def test_normalized_key_collapses_variants():
    assert normalized_key("Cluny_Abbey") == "cluny abbey"
    assert normalized_key("  Saint-Gall ") == "saint gall"
    assert normalized_key("Église") == "eglise"
    assert normalized_key("Cluny_Abbey") == normalized_key("Cluny Abbey")

def test_lexical_guard():
    assert lexical_guard("cluny", "cluny abbey")        # sous-chaîne / token commun
    assert lexical_guard("union europeenne", "ue")      # acronyme
    assert not lexical_guard("cluny", "paris")          # aucun lien lexical

def _emb(mapping):
    async def embed_fn(text):
        return mapping[text]
    return embed_fn

async def test_resolver_merges_by_lexical_key(tmp_path):
    st = Store(str(tmp_path / "r.db"), embed_dim=2); st.init_db()
    doc = st.create_document(st.create_set("S"), "d", "text")
    # le 1er resolve embed toujours (création) ; le 2e (même clé) saute l'embed
    r = EntityResolver(st, doc, _emb({"Cluny_Abbey": [1.0, 0.0]}), threshold=0.9)
    a = await r.resolve("Cluny_Abbey")
    b = await r.resolve("Cluny Abbey")   # clé "cluny abbey" déjà connue -> même entité
    assert a == b

async def test_resolver_hybrid_guard_blocks_false_merge(tmp_path):
    st = Store(str(tmp_path / "g.db"), embed_dim=2); st.init_db()
    doc = st.create_document(st.create_set("S"), "d", "text")
    # cosinus élevé (0.99) mais aucun lien lexical -> NE PAS fusionner
    emb = _emb({"Cluny": [1.0, 0.0], "Paris": [0.99, 0.1411]})
    r = EntityResolver(st, doc, emb, threshold=0.9)
    a = await r.resolve("Cluny")
    b = await r.resolve("Paris")
    assert a != b

async def test_resolver_merges_on_embedding_plus_lexical(tmp_path):
    st = Store(str(tmp_path / "m.db"), embed_dim=2); st.init_db()
    doc = st.create_document(st.create_set("S"), "d", "text")
    emb = _emb({"Cluny": [1.0, 0.0], "Cluny abbaye": [0.97, 0.2429]})  # cos ~0.97
    r = EntityResolver(st, doc, emb, threshold=0.9)
    a = await r.resolve("Cluny")
    b = await r.resolve("Cluny abbaye")   # clé differe, mais cos>=seuil ET garde lexical OK
    assert a == b
```

- [ ] **Step 2 : Lancer (échec)** — `uv run pytest tests/test_entities.py -q` → FAIL (`ModuleNotFoundError`).

- [ ] **Step 3 : Implémenter `nerve/entities.py`**

```python
# nerve/entities.py
import re
import unicodedata
from collections import Counter

def normalized_key(name: str) -> str:
    """Clé déterministe : sans accents, casefold, [_\\W]+ -> espace, espaces normalisés."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.casefold()
    s = re.sub(r"[_\W]+", " ", s)
    return " ".join(s.split())

def _acronym(key: str) -> str:
    return "".join(w[0] for w in key.split() if w)

def lexical_guard(key_a: str, key_b: str) -> bool:
    """Vrai si un lien lexical autorise la fusion : sous-chaîne, token commun, ou acronyme."""
    if not key_a or not key_b:
        return False
    if key_a in key_b or key_b in key_a:
        return True
    if set(key_a.split()) & set(key_b.split()):
        return True
    flat_a, flat_b = key_a.replace(" ", ""), key_b.replace(" ", "")
    return _acronym(key_a) == flat_b or _acronym(key_b) == flat_a

def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))

class EntityResolver:
    """Résolution d'entités intra-document : clé lexicale puis garde hybride
    embedding/lexical. Registre en mémoire + persistance store/vec_entities."""

    def __init__(self, store, document_id: int, embed_fn, threshold: float):
        self.store = store
        self.doc = document_id
        self.embed_fn = embed_fn          # async (text) -> list[float]
        self.threshold = threshold
        self._by_key: dict[str, int] = {}            # normalized_key -> entity_id
        self._entities: list[tuple[int, list[float], str]] = []  # (id, vec, key)
        self._surface: dict[int, Counter] = {}       # entity_id -> Counter(surface forms)

    def _note(self, eid: int, name: str) -> None:
        self._surface[eid][name] += 1
        # canonique = forme la plus fréquente (égalité -> la plus courte)
        best = sorted(self._surface[eid].items(), key=lambda kv: (-kv[1], len(kv[0])))[0][0]
        self.store.set_entity_canonical(eid, best)

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

    def _match(self, key: str, vec: list[float]) -> int | None:
        best_eid, best_sim = None, 0.0
        for eid, evec, ekey in self._entities:
            sim = _dot(vec, evec)
            if sim >= self.threshold and lexical_guard(key, ekey) and sim > best_sim:
                best_eid, best_sim = eid, sim
        return best_eid
```

- [ ] **Step 4 : Lancer (succès)** — `uv run pytest tests/test_entities.py -q` → passed (5 tests).

- [ ] **Step 5 : Commit**

```bash
git add nerve/entities.py tests/test_entities.py
git commit -m "feat(i1): résolution d'entités (clé lexicale + garde hybride)"
```

---

### Task 7 : `dedup.py` — texte de dedup + FactDeduper

**Files:**
- Create: `nerve/dedup.py`
- Test: `tests/test_dedup.py`

- [ ] **Step 1 : Écrire le test**

```python
# tests/test_dedup.py
from nerve.dedup import dedup_text, FactDeduper

def test_dedup_text_fields():
    f = {"subject": "A", "predicate": "r", "object": "B", "title": "T", "description": "D"}
    assert dedup_text(f, "triple") == "A r B"
    assert dedup_text(f, "title") == "T"
    assert dedup_text(f, "description") == "D"

def _emb(mapping):
    async def embed_fn(text):
        return mapping[text]
    return embed_fn

async def test_deduper_flags_duplicates_not_distinct():
    emb = _emb({"A r B": [1.0, 0.0], "C s D": [0.0, 1.0]})
    d = FactDeduper(emb, threshold=0.9, field="triple")
    is_dup, dup_of, vec = await d.check({"subject": "A", "predicate": "r", "object": "B"})
    assert is_dup is False and dup_of is None
    d.add(101, vec)                                   # 1er fait retenu, id 101
    is_dup2, dup_of2, _ = await d.check({"subject": "A", "predicate": "r", "object": "B"})
    assert is_dup2 is True and dup_of2 == 101         # identique -> doublon de 101
    is_dup3, dup_of3, vec3 = await d.check({"subject": "C", "predicate": "s", "object": "D"})
    assert is_dup3 is False and dup_of3 is None       # orthogonal -> distinct
```

- [ ] **Step 2 : Lancer (échec)** — `uv run pytest tests/test_dedup.py -q` → FAIL (`ModuleNotFoundError`).

- [ ] **Step 3 : Implémenter `nerve/dedup.py`**

```python
# nerve/dedup.py
def dedup_text(fact: dict, field: str = "triple") -> str:
    s, p, o = fact.get("subject", ""), fact.get("predicate", ""), fact.get("object", "")
    t, d = fact.get("title", ""), fact.get("description", "")
    if field == "title":
        return t
    if field == "description":
        return d
    if field == "title+desc":
        return f"{t} {d}".strip()
    if field == "all":
        return f"{t} {d} {s} {p} {o}".strip()
    return f"{s} {p} {o}".strip()   # défaut : triple

def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))

class FactDeduper:
    """Dedup intra-document de faits : working set en mémoire (vecteurs normalisés),
    cosinus = produit scalaire. check() embed le texte de dedup ; add() retient un
    fait non-dup avec son id."""

    def __init__(self, embed_fn, threshold: float, field: str = "triple"):
        self.embed_fn = embed_fn        # async (text) -> list[float]
        self.threshold = threshold
        self.field = field
        self._retained: list[tuple[int, list[float]]] = []   # (fact_id, vec)

    async def check(self, fact: dict) -> tuple[bool, int | None, list[float]]:
        vec = await self.embed_fn(dedup_text(fact, self.field))
        best_id, best_sim = None, 0.0
        for fid, fvec in self._retained:
            sim = _dot(vec, fvec)
            if sim > best_sim:
                best_id, best_sim = fid, sim
        if best_sim >= self.threshold:
            return True, best_id, vec
        return False, None, vec

    def add(self, fact_id: int, vec: list[float]) -> None:
        self._retained.append((fact_id, vec))
```

- [ ] **Step 4 : Lancer (succès)** — `uv run pytest tests/test_dedup.py -q` → passed.

- [ ] **Step 5 : Commit**

```bash
git add nerve/dedup.py tests/test_dedup.py
git commit -m "feat(i1): dedup de faits (FactDeduper, working set en mémoire)"
```

---

### Task 8 : `pipeline.py` — intégration (entités + dedup + vecteurs)

**Files:**
- Modify: `nerve/pipeline.py`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1 : Remplacer le test** `tests/test_pipeline.py` par (LLM ET embeddings stubés) :

```python
# tests/test_pipeline.py
import nerve.pipeline as pipe
from nerve.config import load_config
from nerve.store import Store

async def fake_stream(cfg, messages, **kw):
    # 3 faits : deux identiques (doublon) + un distinct ; entités "Cluny"/"Cluny_Abbey" fusionnent par clé
    yield ('[{"subject":"Cluny","predicate":"a_pour","object":"Scriptorium"},'
           '{"subject":"Cluny_Abbey","predicate":"a_pour","object":"Scriptorium"},'
           '{"subject":"Eudes","predicate":"copie","object":"Manuscrits"}]')

async def fake_embed(cfg, texts, **kw):
    # vecteurs déterministes : texte identique -> vecteur identique
    table = {
        "Cluny": [1.0, 0.0, 0.0], "Cluny_Abbey": [1.0, 0.0, 0.0],
        "Scriptorium": [0.0, 1.0, 0.0], "Eudes": [0.0, 0.0, 1.0],
        "Manuscrits": [0.7, 0.0, 0.714],
        "Cluny a_pour Scriptorium": [1.0, 0.0, 0.0],
        "Cluny_Abbey a_pour Scriptorium": [1.0, 0.0, 0.0],   # = au 1er -> doublon
        "Eudes copie Manuscrits": [0.0, 1.0, 0.0],
    }
    return [table[t] for t in texts]

async def test_run_extraction_dedups_and_resolves(tmp_path, monkeypatch):
    monkeypatch.setattr(pipe, "stream_chat", fake_stream)
    monkeypatch.setattr(pipe, "embed", fake_embed)
    st = Store(str(tmp_path / "p.db"), embed_dim=3); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text")
    cfg = load_config()
    events = [e async for e in pipe.run_extraction(cfg, st, doc_id, "un texte")]
    assert events[-1]["type"] == "done"
    doc = st.get_document(doc_id)
    assert doc["total_facts"] == 3
    assert doc["duplicate_facts"] == 1           # le 2e fait est un doublon du 1er
    assert doc["unique_facts"] == 2
    facts = st.get_facts(doc_id)                 # non-dup
    assert len(facts) == 2
    # 4 entités distinctes : Cluny(=Cluny_Abbey fusionné), Scriptorium, Eudes, Manuscrits.
    # Le pipeline résout les entités AVANT le test de dedup ; le 2e fait (doublon)
    # ne crée donc aucune entité neuve (Cluny_Abbey fusionne dans Cluny, Scriptorium existe).
    n_ent = st.conn.execute("SELECT count(*) FROM entities").fetchone()[0]
    assert n_ent == 4
```

- [ ] **Step 2 : Lancer (échec)** — `uv run pytest tests/test_pipeline.py -q` → FAIL (`pipe.embed` absent / comportement).

- [ ] **Step 3 : Réécrire `nerve/pipeline.py`**

```python
# nerve/pipeline.py
from typing import AsyncGenerator
import httpx
from nerve.config import Config
from nerve.store import Store
from nerve.textutil import chunk_text
from nerve.extract import build_messages, FactStreamParser, FACT_RESPONSE_FORMAT
from nerve.llm import stream_chat
from nerve.embeddings import embed
from nerve.entities import EntityResolver
from nerve.dedup import FactDeduper

async def run_extraction(cfg: Config, store: Store, doc_id: int, text: str,
                        *, client=None) -> AsyncGenerator[dict, None]:
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=None)

    async def embed_one(s: str) -> list[float]:
        return (await embed(cfg.embed, [s], client=client))[0]

    resolver = EntityResolver(store, doc_id, embed_one, cfg.entity_threshold)
    deduper = FactDeduper(embed_one, cfg.dedup_threshold, field=cfg.dedup_field)
    try:
        for ci, chunk in enumerate(chunk_text(text)):
            parser = FactStreamParser()
            msgs = build_messages(chunk)
            async for delta in stream_chat(
                cfg.llm, msgs, client=client,
                response_format=FACT_RESPONSE_FORMAT, temperature=0.7,
            ):
                for fact in parser.feed(delta):
                    if not fact.get("subject") or not fact.get("object"):
                        continue
                    sid = await resolver.resolve(fact["subject"])
                    oid = await resolver.resolve(fact["object"])
                    is_dup, dup_of, vec = await deduper.check(fact)
                    fid = store.add_fact(
                        doc_id, fact, is_duplicate=is_dup, dup_of_id=dup_of,
                        subject_entity_id=sid, object_entity_id=oid)
                    if not is_dup:
                        deduper.add(fid, vec)
                        store.add_fact_vector(fid, vec)
                    yield {"type": "fact", "fact": {**fact, "id": fid},
                           "is_duplicate": is_dup}
            yield {"type": "round_end", "chunk": ci}
        store.finish_document(doc_id)
        doc = store.get_document(doc_id)
        yield {"type": "done", "total_facts": doc["total_facts"],
               "unique_facts": doc["unique_facts"],
               "duplicate_facts": doc["duplicate_facts"]}
    except Exception as e:  # remonter l'échec sans l'avaler (embeddings KO -> fail loud)
        store.finish_document(doc_id, error=str(e))
        yield {"type": "error", "message": str(e)}
        raise
    finally:
        if owns_client:
            await client.aclose()
```

- [ ] **Step 4 : Lancer (succès)** — `uv run pytest tests/test_pipeline.py -q` → passed (avec l'assertion `n_ent == 4`).

- [ ] **Step 5 : Commit**

```bash
git add nerve/pipeline.py tests/test_pipeline.py
git commit -m "feat(i1): pipeline résout les entités + déduplique + remplit les vecteurs"
```

---

### Task 9 : `api.py` — compteurs + faits non-dup

**Files:**
- Modify: `nerve/api.py`
- Test: `tests/test_api.py`

- [ ] **Step 1 : Mettre à jour le test** `tests/test_api.py` — remplacer `fake_stream` et le corps de `test_create_document_and_get_facts`, et stubber les embeddings :

```python
# tests/test_api.py
import nerve.pipeline as pipe
from fastapi.testclient import TestClient

async def fake_stream(cfg, messages, **kw):
    yield ('[{"subject":"Chat","predicate":"dort_sur","object":"Tapis"},'
           '{"subject":"Chat","predicate":"dort_sur","object":"Tapis"}]')  # 2e = doublon

async def fake_embed(cfg, texts, **kw):
    base = {"Chat": [1.0, 0.0], "Tapis": [0.0, 1.0],
            "Chat dort_sur Tapis": [1.0, 0.0]}
    return [base.get(t, [0.5, 0.5]) for t in texts]

def test_create_document_and_get_facts(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(pipe, "stream_chat", fake_stream)
    monkeypatch.setattr(pipe, "embed", fake_embed)
    import importlib, nerve.api as api
    importlib.reload(api)
    client = TestClient(api.app)
    r = client.post("/api/documents", json={"title": "t", "text": "le chat dort"})
    assert r.status_code == 200
    body = r.json()
    doc_id = body["document_id"]
    assert body["total_facts"] == 2
    assert body["unique_facts"] == 1
    assert body["duplicate_facts"] == 1
    facts = client.get(f"/api/documents/{doc_id}/facts").json()["facts"]
    assert len(facts) == 1                       # le doublon est exclu
    assert facts[0]["subject_canonical"] == "Chat"
    assert client.get("/").status_code == 200
```

> Garder le test `test_get_facts_unknown_document_returns_404` ajouté au Plan 1 tel quel (il stubbe déjà tout ce qu'il faut).

- [ ] **Step 2 : Lancer (échec)** — `uv run pytest tests/test_api.py -q` → FAIL (clés `unique_facts`/`duplicate_facts` absentes).

- [ ] **Step 3 : Modifier `create_document`** dans `nerve/api.py` (le `return`) :

```python
    doc = store.get_document(doc_id)
    return {"document_id": doc_id, "total_facts": doc["total_facts"],
            "unique_facts": doc["unique_facts"],
            "duplicate_facts": doc["duplicate_facts"],
            "status": doc["status"]}
```

(`get_facts` côté store filtre déjà les doublons par défaut — Task 5 — donc la route `GET /api/documents/{doc_id}/facts` n'a pas besoin de changer.)

- [ ] **Step 4 : Lancer (succès)** — `uv run pytest tests/test_api.py -q` → passed (les 2 tests).

- [ ] **Step 5 : Commit**

```bash
git add nerve/api.py tests/test_api.py
git commit -m "feat(i1): API remonte unique/duplicate, n'expose que les faits non-dup"
```

---

### Task 10 : `web/index.html` — nœuds par nom canonique

**Files:**
- Modify: `nerve/web/index.html` (la fonction `render`)

- [ ] **Step 1 : Modifier `render`** pour utiliser les noms canoniques (avec repli) — remplacer la boucle `for(const f of facts)` :

```javascript
function render(facts){
  const nodes=new Map(), links=[];
  for(const f of facts){
    const s = f.subject_canonical || f.subject;
    const o = f.object_canonical || f.object;
    if(!s||!o) continue;
    nodes.set(s,{id:s}); nodes.set(o,{id:o});
    links.push({source:s,target:o,predicate:f.predicate});
  }
  G.graphData({nodes:[...nodes.values()],links});
}
```

- [ ] **Step 2 : Vérifier que la suite reste verte** — `uv run pytest -q` → tout passe (aucun test ne dépend du HTML hormis `GET /` = 200).

- [ ] **Step 3 : Commit**

```bash
git add nerve/web/index.html
git commit -m "feat(i1): le graphe utilise les noms d'entités canoniques"
```

---

### Task 11 : Calibration des seuils + smoke réel (manuel, bge-m3)

**Files:** `scripts/calibrate_thresholds.py` (CREATE) — outil de calibration hors suite de tests.

- [ ] **Step 1 : Prérequis** — `ollama pull bge-m3` (qwen3.6 déjà présent). Vérifier : `ollama list` montre `bge-m3`.

- [ ] **Step 2 : Écrire le script de calibration** `scripts/calibrate_thresholds.py`

```python
# scripts/calibrate_thresholds.py
"""Affiche les cosinus bge-m3 sur des paires étiquetées pour choisir les seuils."""
import asyncio
from nerve.config import load_config
from nerve.embeddings import embed

# (texte_a, texte_b, devraient_fusionner?)
ENTITY_PAIRS = [
    ("Cluny", "Cluny Abbey", True),
    ("Cluny", "Abbaye de Cluny", True),
    ("Saint-Gall", "Abbaye de Saint-Gall", True),
    ("Notker le Bègue", "Notker le Chauve", False),
    ("Cluny", "Paris", False),
]
FACT_PAIRS = [
    ("Cluny a_pour_scriptorium Scriptorium", "Cluny possède un scriptorium", True),
    ("Eudes copie Manuscrits", "Othmar fonde Saint-Gall", False),
]

async def main():
    cfg = load_config()
    async def cos(a, b):
        va, vb = await embed(cfg.embed, [a, b])
        return sum(x * y for x, y in zip(va, vb))
    print("== entités (ENTITY_THRESHOLD) ==")
    for a, b, merge in ENTITY_PAIRS:
        print(f"  {await cos(a,b):.3f}  attendu={'fusion' if merge else 'distinct':8}  {a!r} / {b!r}")
    print("== faits (DEDUP_THRESHOLD) ==")
    for a, b, dup in FACT_PAIRS:
        print(f"  {await cos(a,b):.3f}  attendu={'dup' if dup else 'distinct':8}  {a!r} / {b!r}")

asyncio.run(main())
```

Run: `uv run python scripts/calibrate_thresholds.py`
Expected: une colonne de cosinus. **Choisir** `ENTITY_THRESHOLD` entre le plus haut « distinct » et le plus bas « fusion » (idem `DEDUP_THRESHOLD` pour les faits). Si les défauts (0.80 / 0.85) ne séparent pas bien, les ajuster via env et redocumenter dans le spec §9.

- [ ] **Step 3 : Lancer toute la suite** — `uv run pytest -q` → tous verts.

- [ ] **Step 4 : Smoke réel de bout en bout**

```bash
uv run nerve   # dans un terminal (ou en arrière-plan)
```
Puis ouvrir `http://127.0.0.1:3000`, coller le paragraphe Cluny/Saint-Gall du smoke Plan 1, **Extraire**.
Expected : `Cluny`/`Cluny Abbey` apparaissent comme **un seul nœud** (plus de `Cluny_Abbey` séparé) ; les faits redondants sont absents du graphe ; la réponse `POST` montre `duplicate_facts > 0` quand le texte en contient. **Ceci valide la qualité du graphe de bout en bout** (fusion des nœuds + dedup, contre bge-m3 réel).

- [ ] **Step 5 : Commit**

```bash
git add scripts/calibrate_thresholds.py
git commit -m "test(i1): outil de calibration des seuils + smoke réel validé (bge-m3)"
```

---

## Auto-revue (vérifiée contre le spec I-1)

**Couverture spec :**
- §2.1 prompt canonique → Task 3. ✓
- §2.2 résolution d'entités (clé lexicale + garde hybride, intra-doc, label le plus fréquent) → Task 6. ✓
- §3.1 `embeddings.py` normalisé → Task 2. ✓
- §3.2 dedup de faits (`dedup_text` champ configurable, cosinus, seuil) → Task 7. ✓
- §3.3 working sets mémoire + persistance `vec_facts`/`vec_entities` → Task 4 (persistance) + Task 8 (mémoire/flux). ✓
- §4 modèle de données (colonnes, `entities`, `vec_entities`, remplissage `vec_facts`) → Task 4, 5. ✓
- §5 flux pipeline (résoudre → dedup → store → vecteurs) → Task 8. ✓
- §6.1 calibration → Task 11. ✓
- §6.2 tests (embedding factice + MockTransport + smoke réel) → Tasks 2, 6, 7, 8, 11. ✓
- §6.3 fail-loud embeddings absents → Task 8 (le `except ... raise` remonte l'échec d'`embed`). ✓
- Config (seuils, champ) → Task 1. ✓

**Placeholders :** aucun — chaque étape porte code/commande réels. Les seuils 0.80/0.85 sont des **défauts de départ** explicitement calibrés en Task 11 (pas des TBD).

**Cohérence des types/signatures :** `embed(cfg, texts, *, client)` (Task 2) appelé en Task 8 via `embed_one` ; `EntityResolver(store, doc, embed_fn, threshold).resolve` (Task 6) et `FactDeduper(embed_fn, threshold, field).check/add` (Task 7) utilisés tels quels en Task 8 ; `store.add_fact(..., is_duplicate, dup_of_id, subject_entity_id, object_entity_id)` (Task 5) appelé en Task 8 ; `get_facts(..., include_duplicates=False)` + `subject_canonical` (Task 5) consommés en Task 9/10. Le point de patch des tests (`nerve.pipeline.stream_chat` et `nerve.pipeline.embed`) est cohérent avec les imports de Task 8.

**Reporté (hors I-1, conforme au découpage) :** SSE live + scheduler (I-3) ; endpoints `/search` et `/transverse` qui *consommeront* `vec_facts`/`vec_entities` (I-4) ; visu avancée — modes de couleur, graphology, étiquettes, thèmes (I-5) ; fusion d'entités cross-document (hors v1).
