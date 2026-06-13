# nerve — Plan 1 : squelette de bout en bout (walking skeleton)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Livrer un pipeline minimal mais complet — coller un texte → extraction de triplets via un LLM Ollama → stockage SQLite → rendu d'un graphe force-directed dans le navigateur — qui dé-risque tôt les deux points sensibles (sortie structurée OpenAI-compatible, chargement de `sqlite-vec` sur macOS).

**Architecture:** Backend Python (FastAPI) lancé via `uv`, sans Docker. Modules à responsabilité unique : `config` (providers configurables), `textutil` (chunking), `extract` (schéma + parse incrémental des triplets), `llm` (client de chat OpenAI-compatible streamé), `store` (SQLite + table `sqlite-vec`), `pipeline` (orchestration), `api` (routes + page web), `web/` (rendu force-graph). Dedup, embeddings, scheduler, transcodeurs distants et modèle 3-niveaux sont **hors de ce plan** (Plan 2).

**Tech Stack:** Python 3.11+, uv, FastAPI, httpx, sqlite3 + sqlite-vec, force-graph.js (CDN), pytest + pytest-asyncio. LLM par défaut : Ollama `qwen3.6` (déjà installé), via API OpenAI-compatible.

---

## Périmètre & fichiers

```
nerve/
  pyproject.toml          # projet uv + deps + config pytest
  nerve/
    __init__.py
    config.py             # providers LLM/embeddings configurables (env)
    textutil.py           # chunk_text (pur)
    extract.py            # FACT_SCHEMA + FACT_RESPONSE_FORMAT + build_messages + FactStreamParser
    llm.py                # stream_chat : client chat OpenAI-compatible (httpx, streamé)
    store.py              # SQLite : schéma + sqlite-vec + CRUD documents/faits
    pipeline.py           # run_extraction : texte → chunks → LLM → parse → store
    api.py                # FastAPI : POST /api/documents, GET facts, GET / ; main()
    web/
      index.html          # textarea → POST → rendu force-graph (palette scriptorium)
  tests/
    test_config.py
    test_textutil.py
    test_extract.py
    test_llm.py
    test_store.py
    test_pipeline.py
    test_api.py
```

**Note (Plan 1)** : le rendu se fait **après** la fin de l'extraction (la page `POST` puis `GET` les faits). Le **streaming live (SSE)** arrive au Plan 2 avec le scheduler. La table `vec_facts` est créée (pour valider le chargement de `sqlite-vec`) mais **non remplie** dans ce plan.

---

### Task 1 : Scaffolding uv + dépendances

**Files:**
- Create: `pyproject.toml`
- Create: `nerve/__init__.py`

- [ ] **Step 1 : Écrire `pyproject.toml`**

```toml
[project]
name = "nerve"
version = "0.1.0"
description = "Extracteur et visualiseur de graphes de connaissances"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.110",
  "uvicorn[standard]>=0.29",
  "httpx>=0.27",
  "numpy>=1.26",
  "python-multipart>=0.0.9",
  "pypdf>=4.0",
  "trafilatura>=1.8",
  "sqlite-vec>=0.1.1",
]

[project.scripts]
nerve = "nerve.api:main"

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.23"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

- [ ] **Step 2 : Créer le package**

```bash
mkdir -p nerve tests
printf '"""nerve — graphe de connaissances."""\n' > nerve/__init__.py
```

- [ ] **Step 3 : Synchroniser l'environnement**

Run: `uv sync`
Expected: création de `.venv`, installation des deps + groupe dev, sans erreur.

- [ ] **Step 4 : Vérifier les imports clés (dont le risque `sqlite-vec`)**

Run: `uv run python -c "import fastapi, httpx, trafilatura, sqlite_vec; print('imports ok')"`
Expected: `imports ok`. Si `sqlite_vec` échoue à l'import, corriger la version avant d'aller plus loin.

- [ ] **Step 5 : Commit**

```bash
git add pyproject.toml uv.lock nerve/__init__.py
git commit -m "chore: scaffolding uv + dépendances de base"
```

---

### Task 2 : `config.py` — providers configurables

**Files:**
- Create: `nerve/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1 : Écrire le test**

```python
# tests/test_config.py
import importlib
from nerve import config as cfgmod

def test_defaults(monkeypatch):
    for k in ("LLM_BASE_URL","LLM_MODEL","EMBED_MODEL","EMBED_DIM","NERVE_DATA_DIR"):
        monkeypatch.delenv(k, raising=False)
    c = cfgmod.load_config()
    assert c.llm.base_url == "http://localhost:11434/v1"
    assert c.llm.model == "qwen3.6"
    assert c.embed.model == "bge-m3"
    assert c.embed_dim == 1024
    assert c.db_path.endswith("nerve.db")

def test_env_override(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("LLM_MODEL", "anthropic/claude-3.5")
    monkeypatch.setenv("EMBED_DIM", "768")
    c = cfgmod.load_config()
    assert c.llm.base_url == "https://openrouter.ai/api/v1"
    assert c.llm.model == "anthropic/claude-3.5"
    assert c.embed_dim == 768
```

- [ ] **Step 2 : Lancer le test (échec attendu)**

Run: `uv run pytest tests/test_config.py -q`
Expected: FAIL (`ModuleNotFoundError` / attributs absents).

- [ ] **Step 3 : Implémenter `config.py`**

```python
# nerve/config.py
import os
from dataclasses import dataclass

@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    api_key: str
    model: str

@dataclass(frozen=True)
class Config:
    llm: ProviderConfig
    embed: ProviderConfig
    embed_dim: int
    data_dir: str
    db_path: str
    port: int

def load_config() -> Config:
    data_dir = os.environ.get("NERVE_DATA_DIR", "data")
    llm = ProviderConfig(
        base_url=os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1"),
        api_key=os.environ.get("LLM_API_KEY", "ollama"),
        model=os.environ.get("LLM_MODEL", "qwen3.6"),
    )
    embed = ProviderConfig(
        base_url=os.environ.get("EMBED_BASE_URL", "http://localhost:11434/v1"),
        api_key=os.environ.get("EMBED_API_KEY", "ollama"),
        model=os.environ.get("EMBED_MODEL", "bge-m3"),
    )
    return Config(
        llm=llm, embed=embed,
        embed_dim=int(os.environ.get("EMBED_DIM", "1024")),
        data_dir=data_dir,
        db_path=os.path.join(data_dir, "nerve.db"),
        port=int(os.environ.get("NERVE_PORT", "3000")),
    )
```

- [ ] **Step 4 : Lancer le test (succès attendu)**

Run: `uv run pytest tests/test_config.py -q`
Expected: 2 passed.

- [ ] **Step 5 : Commit**

```bash
git add nerve/config.py tests/test_config.py
git commit -m "feat: config providers LLM/embeddings configurables"
```

---

### Task 3 : `textutil.py` — chunking sans perte

**Files:**
- Create: `nerve/textutil.py`
- Test: `tests/test_textutil.py`

- [ ] **Step 1 : Écrire le test**

```python
# tests/test_textutil.py
from nerve.textutil import chunk_text

def test_short_text_single_chunk():
    assert chunk_text("bonjour le monde") == ["bonjour le monde"]

def test_empty():
    assert chunk_text("   ") == []

def test_long_text_splits_without_loss():
    para = ("Phrase numéro un. " * 50).strip()
    text = "\n\n".join([para] * 40)            # ~ bien au-delà de la limite
    chunks = chunk_text(text, limit=2000)
    assert len(chunks) > 1
    assert all(len(c) <= 2000 for c in chunks)
    # aucun mot perdu : tous les mots du texte se retrouvent dans la concat
    original_words = set(text.split())
    joined_words = set(" ".join(chunks).split())
    assert original_words.issubset(joined_words)
```

- [ ] **Step 2 : Lancer le test (échec attendu)**

Run: `uv run pytest tests/test_textutil.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3 : Implémenter `textutil.py`**

```python
# nerve/textutil.py
def chunk_text(text: str, limit: int = 24000) -> list[str]:
    """Découpe le texte en morceaux <= limit, sans troncature.
    Recule vers une frontière de paragraphe, puis phrase, puis mot."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if n - i <= limit:
            tail = text[i:].strip()
            if tail:
                chunks.append(tail)
            break
        window = text[i:i + limit]
        floor = int(limit * 0.5)
        cut = window.rfind("\n\n")
        if cut < floor:
            cut = window.rfind("\n")
        if cut < floor:
            cut = window.rfind(". ")
            if cut != -1:
                cut += 1  # garder le point
        if cut < floor:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit
        piece = text[i:i + cut].strip()
        if piece:
            chunks.append(piece)
        i += cut
    return chunks
```

- [ ] **Step 4 : Lancer le test (succès attendu)**

Run: `uv run pytest tests/test_textutil.py -q`
Expected: passed.

- [ ] **Step 5 : Commit**

```bash
git add nerve/textutil.py tests/test_textutil.py
git commit -m "feat: chunk_text (découpage sans perte)"
```

---

### Task 4 : `extract.py` — schéma + parse incrémental

**Files:**
- Create: `nerve/extract.py`
- Test: `tests/test_extract.py`

- [ ] **Step 1 : Écrire le test**

```python
# tests/test_extract.py
from nerve.extract import FactStreamParser, build_messages, FACT_RESPONSE_FORMAT

def test_parses_two_complete_objects():
    p = FactStreamParser()
    s = '[{"subject":"A","predicate":"r","object":"B"},'\
        '{"subject":"C","predicate":"s","object":"D"}]'
    facts = p.feed(s)
    assert [f["subject"] for f in facts] == ["A", "C"]

def test_yields_objects_incrementally():
    p = FactStreamParser()
    assert p.feed('[{"subject":"A","predicate":"r",') == []      # objet incomplet
    facts = p.feed('"object":"B"}]')
    assert len(facts) == 1 and facts[0]["object"] == "B"

def test_ignores_braces_inside_strings():
    p = FactStreamParser()
    facts = p.feed('[{"subject":"x {y}","predicate":"r","object":"z"}]')
    assert facts[0]["subject"] == "x {y}"

def test_build_messages_includes_text():
    msgs = build_messages("le chat dort")
    assert msgs[-1]["role"] == "user"
    assert "le chat dort" in msgs[-1]["content"]
    assert FACT_RESPONSE_FORMAT["type"] == "json_schema"
```

- [ ] **Step 2 : Lancer le test (échec attendu)**

Run: `uv run pytest tests/test_extract.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3 : Implémenter `extract.py`**

```python
# nerve/extract.py
import json

FACT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "subject": {"type": "string"},
        "predicate": {"type": "string"},
        "object": {"type": "string"},
        "evidence_span": {"type": "string"},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["subject", "predicate", "object"],
}

# Sortie structurée OpenAI-compatible. Support variable selon provider :
# le parseur ci-dessous reste le filet si le provider ne l'honore pas.
FACT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "facts",
        "strict": False,
        "schema": {"type": "array", "items": FACT_SCHEMA},
    },
}

SYSTEM_PROMPT = (
    "Tu extrais des faits atomiques d'un document sous forme de triplets "
    "(subject, predicate, object). Le subject et l'object sont des entités ou "
    "valeurs canoniques et courtes (pas des phrases) pour que les nœuds se "
    "connectent. predicate est une relation précise en snake_case (<=32 car.). "
    "Pour chaque fait, ajoute title, description, evidence_span (citation "
    "verbatim), confidence (0-100) et tags. Réponds UNIQUEMENT par un tableau "
    "JSON d'objets, sans texte autour."
)

def build_messages(text: str, extra: str = "") -> list[dict]:
    user = (extra + "\n\n" if extra else "") + "Document :\n\n" + text
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]

class FactStreamParser:
    """Extrait les objets JSON {..} équilibrés d'un flux de texte, au fil de
    l'eau. Ignore le tableau englobant et le texte hors objets."""

    def __init__(self) -> None:
        self.buf = ""
        self.pos = 0

    def feed(self, text: str) -> list[dict]:
        self.buf += text
        out: list[dict] = []
        i = self.pos
        depth = 0
        start = None
        instr = False
        esc = False
        while i < len(self.buf):
            c = self.buf[i]
            if instr:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    instr = False
            else:
                if c == '"':
                    instr = True
                elif c == "{":
                    if depth == 0:
                        start = i
                    depth += 1
                elif c == "}":
                    if depth > 0:
                        depth -= 1
                        if depth == 0 and start is not None:
                            try:
                                out.append(json.loads(self.buf[start:i + 1]))
                            except json.JSONDecodeError:
                                pass
                            self.pos = i + 1
                            start = None
            i += 1
        return out
```

- [ ] **Step 4 : Lancer le test (succès attendu)**

Run: `uv run pytest tests/test_extract.py -q`
Expected: passed.

- [ ] **Step 5 : Commit**

```bash
git add nerve/extract.py tests/test_extract.py
git commit -m "feat: schéma de faits + parse incrémental des triplets"
```

---

### Task 5 : `llm.py` — client chat OpenAI-compatible streamé

**Files:**
- Create: `nerve/llm.py`
- Test: `tests/test_llm.py`

- [ ] **Step 1 : Écrire le test (httpx MockTransport)**

```python
# tests/test_llm.py
import httpx
from nerve.config import ProviderConfig
from nerve.llm import stream_chat

async def test_stream_yields_content_deltas():
    body = (
        'data: {"choices":[{"delta":{"content":"Bon"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"jour"}}]}\n\n'
        'data: [DONE]\n\n'
    )
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(200, text=body)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = ProviderConfig("http://x/v1", "k", "m")
    out = []
    async for delta in stream_chat(cfg, [{"role": "user", "content": "salut"}], client=client):
        out.append(delta)
    assert "".join(out) == "Bonjour"
```

- [ ] **Step 2 : Lancer le test (échec attendu)**

Run: `uv run pytest tests/test_llm.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3 : Implémenter `llm.py`**

```python
# nerve/llm.py
import json
from typing import AsyncGenerator
import httpx
from nerve.config import ProviderConfig

async def stream_chat(
    cfg: ProviderConfig,
    messages: list[dict],
    *,
    client: httpx.AsyncClient | None = None,
    response_format: dict | None = None,
    **params,
) -> AsyncGenerator[str, None]:
    """Stream les deltas de contenu d'un endpoint /chat/completions
    OpenAI-compatible (Ollama, OpenRouter, OpenAI...)."""
    payload = {"model": cfg.model, "messages": messages, "stream": True, **params}
    if response_format:
        payload["response_format"] = response_format
    headers = {"Authorization": f"Bearer {cfg.api_key}"}
    url = f"{cfg.base_url.rstrip('/')}/chat/completions"
    owns = client is None
    client = client or httpx.AsyncClient(timeout=None)
    try:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices") or [{}]
                delta = (choices[0].get("delta") or {}).get("content")
                if delta:
                    yield delta
    finally:
        if owns:
            await client.aclose()
```

- [ ] **Step 4 : Lancer le test (succès attendu)**

Run: `uv run pytest tests/test_llm.py -q`
Expected: passed.

- [ ] **Step 5 : Commit**

```bash
git add nerve/llm.py tests/test_llm.py
git commit -m "feat: client chat OpenAI-compatible streamé (httpx)"
```

---

### Task 6 : `store.py` — SQLite + sqlite-vec + CRUD

**Files:**
- Create: `nerve/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1 : Écrire le test**

```python
# tests/test_store.py
from nerve.store import Store

def test_create_and_read_facts(tmp_path):
    st = Store(str(tmp_path / "t.db"), embed_dim=8)
    st.init_db()
    set_id = st.create_set("Scriptorium")
    doc_id = st.create_document(set_id, title="doc", source_kind="text")
    f1 = st.add_fact(doc_id, {"subject": "A", "predicate": "r", "object": "B",
                              "confidence": 90, "tags": ["x"]})
    st.add_fact(doc_id, {"subject": "C", "predicate": "s", "object": "D"})
    facts = st.get_facts(doc_id)
    assert len(facts) == 2
    assert facts[0]["subject"] == "A"
    assert facts[0]["tags"] == ["x"]
    assert isinstance(f1, int)
    st.finish_document(doc_id)
    assert st.get_document(doc_id)["status"] == "done"

def test_sqlite_vec_loads(tmp_path):
    """Valide tôt le risque : chargement de l'extension sqlite-vec sur cette machine."""
    st = Store(str(tmp_path / "v.db"), embed_dim=8)
    st.init_db()
    cur = st.conn.execute("SELECT name FROM sqlite_master WHERE name='vec_facts'")
    assert cur.fetchone() is not None
```

- [ ] **Step 2 : Lancer le test (échec attendu)**

Run: `uv run pytest tests/test_store.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3 : Implémenter `store.py`**

```python
# nerve/store.py
import os
import json
import sqlite3
import sqlite_vec

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
  created_at TEXT DEFAULT (datetime('now')), finished_at TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY,
  document_id INTEGER REFERENCES documents(id),
  subject TEXT, predicate TEXT, object TEXT,
  title TEXT, description TEXT, evidence_span TEXT,
  confidence INTEGER, tags_json TEXT, source_file TEXT,
  is_duplicate INTEGER DEFAULT 0, dup_of_id INTEGER,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_facts_doc ON facts(document_id);
"""

class Store:
    def __init__(self, db_path: str, embed_dim: int = 1024):
        self.db_path = db_path
        self.embed_dim = embed_dim
        self.conn: sqlite3.Connection | None = None

    def init_db(self) -> None:
        d = os.path.dirname(self.db_path)
        if d:
            os.makedirs(d, exist_ok=True)
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
        con.executescript(SCHEMA)
        con.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_facts "
            f"USING vec0(fact_id integer primary key, embedding float[{self.embed_dim}])"
        )
        con.commit()
        self.conn = con

    def create_set(self, name: str, description: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO source_sets(name, description) VALUES (?, ?)", (name, description))
        self.conn.commit()
        return cur.lastrowid

    def create_document(self, set_id: int, title: str, source_kind: str,
                        source_ref: str = "", params: dict | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO documents(set_id, title, source_kind, source_ref, params_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (set_id, title, source_kind, source_ref, json.dumps(params or {})))
        self.conn.commit()
        return cur.lastrowid

    def add_fact(self, document_id: int, fact: dict, source_file: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO facts(document_id, subject, predicate, object, title, "
            "description, evidence_span, confidence, tags_json, source_file) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (document_id, fact.get("subject"), fact.get("predicate"),
             fact.get("object"), fact.get("title"), fact.get("description"),
             fact.get("evidence_span"), fact.get("confidence"),
             json.dumps(fact.get("tags", [])), source_file))
        self.conn.execute(
            "UPDATE documents SET total_facts = total_facts + 1 WHERE id = ?",
            (document_id,))
        self.conn.commit()
        return cur.lastrowid

    def get_facts(self, document_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM facts WHERE document_id = ? ORDER BY id", (document_id,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["tags"] = json.loads(d.pop("tags_json") or "[]")
            out.append(d)
        return out

    def get_document(self, document_id: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM documents WHERE id = ?",
                              (document_id,)).fetchone()
        return dict(r) if r else None

    def finish_document(self, document_id: int, error: str = "") -> None:
        self.conn.execute(
            "UPDATE documents SET status = ?, finished_at = datetime('now'), error = ? "
            "WHERE id = ?",
            ("failed" if error else "done", error or None, document_id))
        self.conn.commit()
```

- [ ] **Step 4 : Lancer le test (succès attendu)**

Run: `uv run pytest tests/test_store.py -q`
Expected: passed.
**Si `test_sqlite_vec_loads` échoue** avec `enable_load_extension` indisponible (sqlite du Python sans support d'extensions) : ajouter `pysqlite3-binary` aux deps et remplacer `import sqlite3` par `import pysqlite3 as sqlite3` dans `store.py`, puis relancer. (Risque identifié au spec §8.)

- [ ] **Step 5 : Commit**

```bash
git add nerve/store.py tests/test_store.py
git commit -m "feat: store SQLite + table sqlite-vec + CRUD documents/faits"
```

---

### Task 7 : `pipeline.py` — orchestration texte → faits

**Files:**
- Create: `nerve/pipeline.py`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1 : Écrire le test (LLM stubé via monkeypatch)**

```python
# tests/test_pipeline.py
import nerve.pipeline as pipe
from nerve.config import load_config
from nerve.store import Store

async def fake_stream(cfg, messages, **kw):
    for ch in ['[{"subject":"A","predicate":"r","object":"B"}',
               ',{"subject":"C","predicate":"s","object":"D"}]']:
        yield ch

async def test_run_extraction_stores_and_emits(tmp_path, monkeypatch):
    monkeypatch.setattr(pipe, "stream_chat", fake_stream)
    st = Store(str(tmp_path / "p.db"), embed_dim=8); st.init_db()
    set_id = st.create_set("S"); doc_id = st.create_document(set_id, "d", "text")
    cfg = load_config()
    events = [e async for e in pipe.run_extraction(cfg, st, doc_id, "un texte")]
    facts = [e for e in events if e["type"] == "fact"]
    assert len(facts) == 2
    assert events[-1]["type"] == "done"
    assert len(st.get_facts(doc_id)) == 2
    assert st.get_document(doc_id)["status"] == "done"
```

- [ ] **Step 2 : Lancer le test (échec attendu)**

Run: `uv run pytest tests/test_pipeline.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3 : Implémenter `pipeline.py`**

```python
# nerve/pipeline.py
from typing import AsyncGenerator
from nerve.config import Config
from nerve.store import Store
from nerve.textutil import chunk_text
from nerve.extract import build_messages, FactStreamParser, FACT_RESPONSE_FORMAT
from nerve.llm import stream_chat

async def run_extraction(cfg: Config, store: Store, doc_id: int, text: str,
                        *, client=None) -> AsyncGenerator[dict, None]:
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
                    fid = store.add_fact(doc_id, fact)
                    yield {"type": "fact", "fact": {**fact, "id": fid}}
            yield {"type": "round_end", "chunk": ci}
        store.finish_document(doc_id)
        yield {"type": "done"}
    except Exception as e:  # remonter l'échec sans l'avaler
        store.finish_document(doc_id, error=str(e))
        yield {"type": "error", "message": str(e)}
        raise
```

- [ ] **Step 4 : Lancer le test (succès attendu)**

Run: `uv run pytest tests/test_pipeline.py -q`
Expected: passed.

- [ ] **Step 5 : Commit**

```bash
git add nerve/pipeline.py tests/test_pipeline.py
git commit -m "feat: pipeline texte → chunks → LLM → parse → store"
```

---

### Task 8 : `api.py` — FastAPI (création + faits + page)

**Files:**
- Create: `nerve/api.py`
- Test: `tests/test_api.py`

- [ ] **Step 1 : Écrire le test (TestClient, LLM stubé)**

```python
# tests/test_api.py
import nerve.pipeline as pipe
from fastapi.testclient import TestClient

async def fake_stream(cfg, messages, **kw):
    yield '[{"subject":"Chat","predicate":"dort_sur","object":"Tapis"}]'

def test_create_document_and_get_facts(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(pipe, "stream_chat", fake_stream)
    import importlib, nerve.api as api
    importlib.reload(api)                       # relit NERVE_DATA_DIR
    client = TestClient(api.app)
    r = client.post("/api/documents", json={"title": "t", "text": "le chat dort"})
    assert r.status_code == 200
    doc_id = r.json()["document_id"]
    assert r.json()["total_facts"] == 1
    facts = client.get(f"/api/documents/{doc_id}/facts").json()["facts"]
    assert facts[0]["subject"] == "Chat"
    assert client.get("/").status_code == 200
```

- [ ] **Step 2 : Lancer le test (échec attendu)**

Run: `uv run pytest tests/test_api.py -q`
Expected: FAIL (`ModuleNotFoundError` / app absente).

- [ ] **Step 3 : Implémenter `api.py`**

```python
# nerve/api.py
import os
from fastapi import FastAPI
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
from nerve.config import load_config
from nerve.store import Store
from nerve.pipeline import run_extraction

cfg = load_config()
store = Store(cfg.db_path, embed_dim=cfg.embed_dim)
store.init_db()
app = FastAPI(title="nerve")
WEB = os.path.join(os.path.dirname(__file__), "web")

class CreateDoc(BaseModel):
    title: str = "Sans titre"
    text: str
    set_id: int | None = None
    set_name: str = "Défaut"

@app.post("/api/documents")
async def create_document(body: CreateDoc):
    set_id = body.set_id or store.create_set(body.set_name)
    doc_id = store.create_document(set_id, body.title, "text")
    async for _ in run_extraction(cfg, store, doc_id, body.text):
        pass  # Plan 1 : on consomme jusqu'au bout (SSE live = Plan 2)
    doc = store.get_document(doc_id)
    return {"document_id": doc_id, "total_facts": doc["total_facts"],
            "status": doc["status"]}

@app.get("/api/documents/{doc_id}/facts")
def get_facts(doc_id: int):
    return {"document": store.get_document(doc_id),
            "facts": store.get_facts(doc_id)}

@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"))

def main():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=cfg.port)
```

- [ ] **Step 4 : Lancer le test (succès attendu)**

Run: `uv run pytest tests/test_api.py -q`
Expected: passed. (Le test crée `web/index.html` au Task 9 ; si `GET /` échoue ici car le fichier manque, faire Task 9 d'abord puis relancer — ou créer un `web/index.html` vide temporaire.)

- [ ] **Step 5 : Commit**

```bash
git add nerve/api.py tests/test_api.py
git commit -m "feat: API FastAPI (création document + faits + page)"
```

---

### Task 9 : `web/index.html` — rendu force-graph (palette scriptorium)

**Files:**
- Create: `nerve/web/index.html`

- [ ] **Step 1 : Écrire la page** (sans `innerHTML` — méthodes DOM uniquement)

```html
<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>nerve</title>
<script src="https://unpkg.com/force-graph@1.43.5/dist/force-graph.min.js"></script>
<style>
  :root{--paper:#F4F6FA;--ink:#15202E;--blue:#23537F;--blue-deep:#142E49;--bordeaux:#7C2A38;--line:#D7DFE9}
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--paper);color:var(--ink);font-family:-apple-system,system-ui,sans-serif}
  #top{display:flex;gap:8px;padding:10px;background:var(--blue-deep)}
  #top textarea{flex:1;height:46px;border-radius:6px;border:1px solid var(--line);padding:8px;font-family:inherit}
  #top button{background:#2C77B6;color:#fff;border:0;border-radius:6px;padding:0 16px;cursor:pointer}
  #graph{position:absolute;left:0;right:0;bottom:0;top:66px}
</style>
</head>
<body>
  <div id="top">
    <textarea id="txt" placeholder="Colle un texte à transformer en graphe…"></textarea>
    <button id="go">Extraire</button>
  </div>
  <div id="graph"></div>
<script>
const PAPER="#F4F6FA";
const G=ForceGraph()(document.getElementById("graph"))
  .backgroundColor(PAPER)
  .nodeColor(()=> "#23537F")
  .nodeLabel(n=> n.id)
  .linkColor(()=> "rgba(35,83,127,0.35)")
  .linkLabel(l=> l.predicate)
  .linkDirectionalArrowLength(3.5).linkDirectionalArrowRelPos(0.92);

function render(facts){
  const nodes=new Map(), links=[];
  for(const f of facts){
    if(!f.subject||!f.object) continue;
    nodes.set(f.subject,{id:f.subject}); nodes.set(f.object,{id:f.object});
    links.push({source:f.subject,target:f.object,predicate:f.predicate});
  }
  G.graphData({nodes:[...nodes.values()],links});
}

document.getElementById("go").addEventListener("click", async ()=>{
  const text=document.getElementById("txt").value.trim(); if(!text) return;
  const r=await fetch("/api/documents",{method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({title:"Coller",text})});
  const {document_id}=await r.json();
  const data=await (await fetch(`/api/documents/${document_id}/facts`)).json();
  render(data.facts);
});
</script>
</body>
</html>
```

- [ ] **Step 2 : Vérifier que la page est servie**

Run: `uv run pytest tests/test_api.py -q`
Expected: passed (le `GET /` renvoie maintenant 200).

- [ ] **Step 3 : Commit**

```bash
git add nerve/web/index.html
git commit -m "feat: page web minimale (force-graph, palette scriptorium)"
```

---

### Task 10 : Smoke end-to-end contre Ollama réel (manuel)

**Files:** aucun (vérification d'intégration).

- [ ] **Step 1 : Vérifier qu'Ollama tourne et que le modèle est là**

Run: `ollama list`
Expected: `qwen3.6` présent. Si Ollama n'est pas lancé : `ollama serve` dans un autre terminal.

- [ ] **Step 2 : Lancer toute la suite de tests**

Run: `uv run pytest -q`
Expected: tous les tests passent.

- [ ] **Step 3 : Démarrer le serveur**

Run: `uv run nerve`
Expected: `Uvicorn running on http://127.0.0.1:3000`.

- [ ] **Step 4 : Smoke manuel**

Ouvrir `http://127.0.0.1:3000`, coller un paragraphe (ex. la description d'un sujet de scriptorium), cliquer **Extraire**. Attendre la fin (Plan 1 = rendu après complétion).
Expected : un graphe force-directed apparaît, nœuds bleus, arêtes étiquetées par prédicat au survol. **Ceci valide la sortie structurée Ollama de bout en bout** (risque spec §8). Si le graphe est vide : inspecter la réponse de `qwen3.6` (le parseur incrémental gère le JSON même non strictement structuré ; ajuster le prompt si le modèle n'émet pas de JSON).

- [ ] **Step 5 : Commit (notes éventuelles)**

```bash
git commit --allow-empty -m "test: smoke end-to-end Plan 1 validé (Ollama + sqlite-vec)"
```

---

## Auto-revue (vérifiée contre le spec)

**Couverture spec (sous-ensemble Plan 1) :**
- Stack Python/FastAPI/uv sans Docker → Task 1, 8. ✓
- LLM provider OpenAI-compatible configurable (Ollama défaut, bascule OpenRouter) → Task 2, 5. ✓
- Génération streamée + sortie structurée (avec parseur en filet) → Task 4, 5, 7. ✓
- Persistance SQLite + validation `sqlite-vec` → Task 6. ✓
- Pipeline texte → extraction → store → rendu → Task 7, 8, 9, 10. ✓
- Palette scriptorium (amorce) → Task 9. ✓
- Risques spec §8 dé-risqués tôt : sortie structurée (Task 10), `sqlite-vec`/macOS (Task 6 + fallback documenté). ✓

**Reporté au Plan 2 (hors périmètre, conforme au découpage annoncé) :** embeddings + dedup, ingestion URL/zip/fichiers + transcodeurs enfichables, scheduler (pause/reprise) + **SSE live**, modèle 3-niveaux complet (navigation sets/transverse + recherche), visu complète (graphology, étiquettes d'arêtes, modes de couleur, clair/sombre).

**Placeholders :** aucun — chaque étape porte du code/commande réels.

**Cohérence des types :** `Config`/`ProviderConfig` (Task 2) utilisés tels quels en Task 5/7/8 ; `Store` méthodes (`init_db`, `create_set`, `create_document`, `add_fact`, `get_facts`, `get_document`, `finish_document`) cohérentes entre Task 6 et leurs appels (Task 7/8) ; `stream_chat` signature identique (Task 5) et son point de patch (`nerve.pipeline.stream_chat`) cohérent avec les tests (Task 7/8) ; `FactStreamParser.feed`, `build_messages`, `FACT_RESPONSE_FORMAT` cohérents (Task 4 → 7).
