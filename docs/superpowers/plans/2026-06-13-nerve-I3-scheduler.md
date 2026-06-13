# Plan 2 / I-3 · Scheduler + SSE live + concurrence — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Passer l'extraction d'un modèle synchrone bloquant à une file FIFO mono-worker asyncio avec diffusion SSE en direct, pause/reprise, reprise après crash, et durcissement concurrence SQLite (WAL).

**Architecture:** Un `Scheduler` asyncio (file `asyncio.Queue` + une tâche worker dans la `lifespan` FastAPI + bus pub/sub par doc). Les endpoints **enfilent** (retour immédiat `queued`) ; le worker exécute `run_extraction` et **émet** chaque event vers les abonnés SSE. La reprise (par chunk) est encapsulée dans `run_extraction` (preload du deduper/resolver depuis la DB). Concurrence : WAL + busy_timeout.

**Tech Stack:** Python 3.11+, FastAPI (lifespan + StreamingResponse SSE), asyncio, SQLite (`sqlite-vec`, WAL), numpy (désérialisation vecteurs), pytest (asyncio auto).

**Réf. spec :** `docs/superpowers/specs/2026-06-13-nerve-I3-scheduler-design.md`.

---

## File Structure

- **Create** `nerve/scheduler.py` — `write_segments`/`load_segments`, classe `Scheduler` (file, pub/sub, worker, pause/resume, reconcile).
- **Create** `tests/test_scheduler.py`.
- **Modify** `nerve/store.py` — WAL/busy_timeout, colonnes `progress_*`, `set_status`/`set_progress`/`list_resumable`/`load_fact_vectors`/`load_entities`, `import numpy`.
- **Modify** `nerve/dedup.py` — `FactDeduper.preload`.
- **Modify** `nerve/entities.py` — `EntityResolver.preload`.
- **Modify** `nerve/pipeline.py` — `run_extraction(..., start_segment, start_chunk)` + skip + `round_end{segment,chunk}` + preload.
- **Modify** `nerve/api.py` — scheduler module-level, `lifespan`, enqueue (refactor `create_document`/`upload`), `pause`/`resume`/`{id}`/`events` (SSE).
- **Modify** `nerve/web/index.html` — consommation SSE.
- **Modify** `tests/test_store.py`, `tests/test_pipeline.py`, `tests/test_api.py`.

Conventions reprises : tests sans réseau (`monkeypatch`), `asyncio_mode=auto` (tests `async def` directs), `TestClient` + `importlib.reload(api)`.

---

## Task 1 : store.py — WAL, progression, statut, list_resumable

**Files:** Modify `nerve/store.py` ; Test `tests/test_store.py`

- [ ] **Step 1 : Tests rouges** — ajouter à `tests/test_store.py` :

```python
def test_wal_enabled(tmp_path):
    st = Store(str(tmp_path / "wal.db"), embed_dim=4); st.init_db()
    assert st.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"

def test_status_and_progress(tmp_path):
    st = Store(str(tmp_path / "p.db"), embed_dim=4); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text")
    assert st.get_document(doc_id)["progress_segment"] == 0
    assert st.get_document(doc_id)["progress_chunk"] == 0
    st.set_status(doc_id, "queued"); assert st.get_document(doc_id)["status"] == "queued"
    st.set_progress(doc_id, 2, 5)
    doc = st.get_document(doc_id)
    assert doc["progress_segment"] == 2 and doc["progress_chunk"] == 5

def test_list_resumable(tmp_path):
    st = Store(str(tmp_path / "lr.db"), embed_dim=4); st.init_db()
    s = st.create_set("S")
    d1 = st.create_document(s, "1", "text"); st.set_status(d1, "running")
    d2 = st.create_document(s, "2", "text"); st.set_status(d2, "queued")
    d3 = st.create_document(s, "3", "text"); st.finish_document(d3)  # done
    d4 = st.create_document(s, "4", "text"); st.set_status(d4, "paused")
    assert st.list_resumable() == [d1, d2]
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_store.py -q` → FAIL (`PRAGMA journal_mode` != wal ; pas de `progress_segment` ; `set_status` absent).

- [ ] **Step 3 : Implémenter** — dans `nerve/store.py` :

Ajouter en tête (après `import sqlite_vec`) : `import numpy as np`

Dans `SCHEMA`, table `documents`, ajouter deux colonnes (après `duplicate_facts INTEGER DEFAULT 0,`) :

```sql
  progress_segment INTEGER DEFAULT 0,
  progress_chunk INTEGER DEFAULT 0,
```

Dans `init_db`, juste après `sqlite_vec.load(con)` + `con.enable_load_extension(False)` et **avant** `con.executescript(SCHEMA)` :

```python
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=5000")
```

Ajouter ces méthodes à la classe `Store` (après `finish_document`) :

```python
    def set_status(self, document_id: int, status: str) -> None:
        self.conn.execute("UPDATE documents SET status = ? WHERE id = ?",
                          (status, document_id))
        self.conn.commit()

    def set_progress(self, document_id: int, segment: int, chunk: int) -> None:
        self.conn.execute(
            "UPDATE documents SET progress_segment = ?, progress_chunk = ? WHERE id = ?",
            (segment, chunk, document_id))
        self.conn.commit()

    def list_resumable(self) -> list[int]:
        rows = self.conn.execute(
            "SELECT id FROM documents WHERE status IN ('running','queued') ORDER BY id"
        ).fetchall()
        return [r["id"] for r in rows]
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_store.py -q` → PASS.
- [ ] **Step 5 : Commit**

```bash
git add nerve/store.py tests/test_store.py
git commit -m "feat(i3): store — WAL/busy_timeout, colonnes progress, set_status/set_progress/list_resumable"
```

---

## Task 2 : store.py — load_fact_vectors / load_entities

**Files:** Modify `nerve/store.py` ; Test `tests/test_store.py`

- [ ] **Step 1 : Tests rouges** — ajouter à `tests/test_store.py` :

```python
def test_load_fact_vectors(tmp_path):
    st = Store(str(tmp_path / "lfv.db"), embed_dim=3); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text")
    f1 = st.add_fact(doc_id, {"subject": "A", "predicate": "r", "object": "B"})
    st.add_fact_vector(f1, [0.1, 0.2, 0.3])
    f2 = st.add_fact(doc_id, {"subject": "A", "predicate": "r", "object": "B"},
                     is_duplicate=True, dup_of_id=f1)            # dup -> pas de vecteur
    rows = st.load_fact_vectors(doc_id)
    assert len(rows) == 1
    fid, vec = rows[0]
    assert fid == f1
    assert all(abs(a - b) < 1e-6 for a, b in zip(vec, [0.1, 0.2, 0.3]))

def test_load_entities(tmp_path):
    st = Store(str(tmp_path / "le.db"), embed_dim=2); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text")
    eid = st.create_entity(doc_id, "Cluny", "cluny"); st.add_entity_vector(eid, [1.0, 0.0])
    st.bump_entity_mention(eid)
    rows = st.load_entities(doc_id)
    assert len(rows) == 1
    rid, canonical, key, mention, vec = rows[0]
    assert (rid, canonical, key) == (eid, "Cluny", "cluny")
    assert mention == 2
    assert all(abs(a - b) < 1e-6 for a, b in zip(vec, [1.0, 0.0]))
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_store.py -q` → FAIL (`load_fact_vectors` absent).

- [ ] **Step 3 : Implémenter** — ajouter à `Store` (après `list_resumable`) :

```python
    def load_fact_vectors(self, document_id: int) -> list[tuple[int, list[float]]]:
        """(fact_id, vecteur) des faits NON-dup du doc, depuis vec_facts."""
        rows = self.conn.execute(
            "SELECT v.fact_id AS fid, v.embedding AS emb FROM vec_facts v "
            "JOIN facts f ON f.id = v.fact_id "
            "WHERE f.document_id = ? AND f.is_duplicate = 0", (document_id,)).fetchall()
        return [(r["fid"], np.frombuffer(r["emb"], dtype=np.float32).tolist()) for r in rows]

    def load_entities(self, document_id: int) -> list[tuple[int, str, str, int, list[float]]]:
        """(id, canonical_name, normalized_key, mention_count, vecteur) du doc."""
        rows = self.conn.execute(
            "SELECT e.id AS id, e.canonical_name AS cn, e.normalized_key AS nk, "
            "e.mention_count AS mc, v.embedding AS emb FROM entities e "
            "JOIN vec_entities v ON v.entity_id = e.id "
            "WHERE e.document_id = ?", (document_id,)).fetchall()
        return [(r["id"], r["cn"], r["nk"], r["mc"],
                 np.frombuffer(r["emb"], dtype=np.float32).tolist()) for r in rows]
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_store.py -q` → PASS.
- [ ] **Step 5 : Commit**

```bash
git add nerve/store.py tests/test_store.py
git commit -m "feat(i3): store — load_fact_vectors/load_entities (désérialisation vecteurs)"
```

---

## Task 3 : preload du deduper et du resolver

**Files:** Modify `nerve/dedup.py`, `nerve/entities.py` ; Test `tests/test_dedup.py`, `tests/test_entities.py`

- [ ] **Step 1 : Tests rouges** —

Ajouter à `tests/test_dedup.py` :

```python
from nerve.dedup import FactDeduper

async def test_deduper_preload_detects_known_dup():
    async def emb(s):
        return [1.0, 0.0] if s == "A r B" else [0.0, 1.0]
    d = FactDeduper(emb, 0.85, field="triple")
    d.preload([(7, [1.0, 0.0])])                       # un fait déjà retenu (fact_id=7)
    is_dup, dup_of, vec = await d.check({"subject": "A", "predicate": "r", "object": "B"})
    assert is_dup and dup_of == 7
```

Ajouter à `tests/test_entities.py` :

```python
from collections import Counter
from nerve.entities import EntityResolver

async def test_resolver_preload_reuses_known_entity():
    class _Store:                                       # store minimal (pas de DB)
        def bump_entity_mention(self, eid): pass
        def set_entity_canonical(self, eid, name): pass
    async def emb(s): return [1.0, 0.0]
    r = EntityResolver(_Store(), 1, emb, 0.75)
    r.preload([(42, "Cluny", "cluny", 3, [1.0, 0.0])])
    eid = await r.resolve("Cluny")                      # clé "cluny" connue -> réutilise 42
    assert eid == 42
    assert r._surface[42] == Counter({"Cluny": 4})      # mention pré-chargée + 1
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_dedup.py tests/test_entities.py -q` → FAIL (`preload` absent).

- [ ] **Step 3 : Implémenter** —

Dans `nerve/dedup.py`, ajouter à `FactDeduper` (après `add`) :

```python
    def preload(self, items: list[tuple[int, list[float]]]) -> None:
        """Pré-remplit le working set à la reprise (fact_id, vecteur normalisé)."""
        self._retained.extend(items)
```

Dans `nerve/entities.py`, ajouter à `EntityResolver` (après `__init__`) :

```python
    def preload(self, rows: list[tuple[int, str, str, int, list[float]]]) -> None:
        """Reconstruit le registre à la reprise : (id, canonical, key, mention, vec)."""
        for eid, canonical, key, mention, vec in rows:
            self._by_key[key] = eid
            self._entities.append((eid, vec, key))
            self._surface[eid] = Counter({canonical: mention})
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_dedup.py tests/test_entities.py -q` → PASS.
- [ ] **Step 5 : Commit**

```bash
git add nerve/dedup.py nerve/entities.py tests/test_dedup.py tests/test_entities.py
git commit -m "feat(i3): FactDeduper.preload + EntityResolver.preload (reprise depuis la DB)"
```

---

## Task 4 : pipeline.py — reprise (start_segment/start_chunk) + round_end enrichi

**Files:** Modify `nerve/pipeline.py` ; Test `tests/test_pipeline.py`

- [ ] **Step 1 : Tests rouges** — ajouter à `tests/test_pipeline.py` (réutilise `fake_stream_one`/`fake_embed` déjà présents) :

```python
async def test_round_end_carries_segment_chunk(tmp_path, monkeypatch):
    monkeypatch.setattr(pipe, "stream_chat", fake_stream_one)
    monkeypatch.setattr(pipe, "embed", fake_embed)
    st = Store(str(tmp_path / "re.db"), embed_dim=3); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "file")
    events = [e async for e in pipe.run_extraction(cfg_re(), st, doc_id, [("x", "a.txt")])]
    re_events = [e for e in events if e["type"] == "round_end"]
    assert re_events and re_events[0]["segment"] == 0 and re_events[0]["chunk"] == 0
    assert re_events[0]["source_file"] == "a.txt"

async def test_resume_skips_segment_and_preloads_dedup(tmp_path, monkeypatch):
    monkeypatch.setattr(pipe, "stream_chat", fake_stream_one)   # émet "Cluny a_pour Scriptorium"
    monkeypatch.setattr(pipe, "embed", fake_embed)
    st = Store(str(tmp_path / "rs.db"), embed_dim=3); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "file")
    # passe initiale : seg0 traité -> 1 fait unique + entités + vecteurs en DB
    [e async for e in pipe.run_extraction(cfg_re(), st, doc_id, [("seg0", "a.txt")])]
    assert st.get_document(doc_id)["unique_facts"] == 1
    # reprise : start_segment=1 -> seg0 SAUTÉ ; seg1 ré-émet le même fait -> doublon via preload
    [e async for e in pipe.run_extraction(cfg_re(), st, doc_id,
        [("seg0", "a.txt"), ("seg1", "b.txt")], start_segment=1)]
    doc = st.get_document(doc_id)
    assert doc["unique_facts"] == 1       # rien de neuf (preload a chargé le fait)
    assert doc["duplicate_facts"] == 1    # exactement 1 (seg0 sauté, sinon ce serait 2)
```

Ajouter aussi en tête de `tests/test_pipeline.py` un helper (si absent) :

```python
def cfg_re():
    return load_config()
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_pipeline.py -q` → FAIL (`round_end` sans `segment` ; `start_segment` inconnu).

- [ ] **Step 3 : Implémenter** — remplacer la fonction `run_extraction` de `nerve/pipeline.py` par :

```python
async def run_extraction(cfg: Config, store: Store, doc_id: int,
                        segments: list[tuple[str, str]], *,
                        start_segment: int = 0, start_chunk: int = 0, client=None
                        ) -> AsyncGenerator[dict, None]:
    """segments = liste de (text, source_file). À la reprise (start_* > 0), le
    deduper/resolver sont pré-chargés depuis la DB et les chunks déjà traités
    (< (start_segment, start_chunk)) sont sautés."""
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=None)

    async def embed_one(s: str) -> list[float]:
        return (await embed(cfg.embed, [s], client=client))[0]

    resolver = EntityResolver(store, doc_id, embed_one, cfg.entity_threshold)
    deduper = FactDeduper(embed_one, cfg.dedup_threshold, field=cfg.dedup_field)
    if start_segment > 0 or start_chunk > 0:
        resolver.preload(store.load_entities(doc_id))
        deduper.preload(store.load_fact_vectors(doc_id))
    try:
        for si, (text, source_file) in enumerate(segments):
            for ci, chunk in enumerate(chunk_text(text)):
                if (si, ci) < (start_segment, start_chunk):
                    continue
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
                            subject_entity_id=sid, object_entity_id=oid,
                            source_file=source_file)
                        if not is_dup:
                            deduper.add(fid, vec)
                            store.add_fact_vector(fid, vec)
                        yield {"type": "fact", "fact": {**fact, "id": fid},
                               "is_duplicate": is_dup, "source_file": source_file}
                yield {"type": "round_end", "segment": si, "chunk": ci,
                       "source_file": source_file}
        store.finish_document(doc_id)
        doc = store.get_document(doc_id)
        yield {"type": "done", "total_facts": doc["total_facts"],
               "unique_facts": doc["unique_facts"],
               "duplicate_facts": doc["duplicate_facts"]}
    except Exception as e:  # fail loud (embeddings KO, etc.)
        store.finish_document(doc_id, error=str(e))
        yield {"type": "error", "message": str(e)}
        raise
    finally:
        if owns_client:
            await client.aclose()
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_pipeline.py -q` → PASS (les tests I-2 + 2 nouveaux).
- [ ] **Step 5 : Commit**

```bash
git add nerve/pipeline.py tests/test_pipeline.py
git commit -m "feat(i3): pipeline — reprise par chunk (start_segment/start_chunk + preload) + round_end{segment,chunk}"
```

---

## Task 5 : scheduler.py — persistance des segments source

**Files:** Create `nerve/scheduler.py` ; Test `tests/test_scheduler.py`

- [ ] **Step 1 : Tests rouges** — créer `tests/test_scheduler.py` :

```python
import nerve.scheduler as sched_mod
from nerve.scheduler import write_segments, load_segments

def test_segments_roundtrip(tmp_path):
    segs = [("texte un", ""), ("texte deux", "b.txt")]
    write_segments(str(tmp_path), 7, segs)
    assert load_segments(str(tmp_path), 7) == segs
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_scheduler.py -q` → FAIL (`No module named 'nerve.scheduler'`).

- [ ] **Step 3 : Implémenter** — créer `nerve/scheduler.py` :

```python
# nerve/scheduler.py
import os
import json
import asyncio
from nerve.pipeline import run_extraction


def _segments_path(data_dir: str, doc_id: int) -> str:
    return os.path.join(data_dir, "inputs", str(doc_id), "segments.jsonl")


def write_segments(data_dir: str, doc_id: int, segments: list[tuple[str, str]]) -> None:
    p = _segments_path(data_dir, doc_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for text, source_file in segments:
            f.write(json.dumps({"text": text, "source_file": source_file}) + "\n")


def load_segments(data_dir: str, doc_id: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    with open(_segments_path(data_dir, doc_id), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                d = json.loads(line)
                out.append((d["text"], d["source_file"]))
    return out
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_scheduler.py -q` → PASS.
- [ ] **Step 5 : Commit**

```bash
git add nerve/scheduler.py tests/test_scheduler.py
git commit -m "feat(i3): scheduler — persistance des segments source (segments.jsonl)"
```

---

## Task 6 : scheduler.py — Scheduler (file, pub/sub, worker)

**Files:** Modify `nerve/scheduler.py` ; Test `tests/test_scheduler.py`

- [ ] **Step 1 : Tests rouges** — ajouter à `tests/test_scheduler.py` :

```python
import asyncio
from nerve.scheduler import Scheduler
from nerve.store import Store
from nerve.config import load_config

async def fake_run_done(cfg, store, doc_id, segments, *, start_segment=0, start_chunk=0, client=None):
    yield {"type": "fact", "fact": {"subject": "A", "predicate": "r", "object": "B", "id": 1},
           "is_duplicate": False, "source_file": ""}
    yield {"type": "round_end", "segment": 0, "chunk": 0, "source_file": ""}
    store.finish_document(doc_id)
    d = store.get_document(doc_id)
    yield {"type": "done", "total_facts": d["total_facts"],
           "unique_facts": d["unique_facts"], "duplicate_facts": d["duplicate_facts"]}

async def test_worker_processes_and_marks_done(tmp_path):
    st = Store(str(tmp_path / "w.db"), embed_dim=2); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text")
    write_segments(str(tmp_path), doc_id, [("texte", "")])
    sched = Scheduler(load_config(), st, run=fake_run_done, data_dir=str(tmp_path))
    sub = sched.subscribe(doc_id)
    sched.start(); sched.enqueue(doc_id)
    types = []
    try:
        while True:
            ev = await asyncio.wait_for(sub.get(), timeout=2)
            types.append(ev.get("type"))
            if ev.get("type") == "done":
                break
    finally:
        await sched.stop()
    assert "fact" in types and "done" in types
    doc = st.get_document(doc_id)
    assert doc["status"] == "done"
    assert doc["progress_chunk"] == 1          # set_progress(0, 0+1) sur round_end
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_scheduler.py -q` → FAIL (`Scheduler` absent).

- [ ] **Step 3 : Implémenter** — ajouter à `nerve/scheduler.py` (après `load_segments`) :

```python
class Scheduler:
    """File FIFO mono-worker + bus pub/sub par doc. Le worker exécute run_extraction
    et émet chaque event vers les abonnés (SSE)."""

    def __init__(self, cfg, store, *, run=run_extraction, data_dir=None):
        self.cfg = cfg
        self.store = store
        self._run = run
        self.data_dir = data_dir if data_dir is not None else cfg.data_dir
        self.queue: asyncio.Queue = asyncio.Queue()
        self._subs: dict[int, list[asyncio.Queue]] = {}
        self._pause: set[int] = set()
        self._task = None

    def enqueue(self, doc_id: int) -> None:
        self.store.set_status(doc_id, "queued")
        self.queue.put_nowait(doc_id)

    def subscribe(self, doc_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=10000)
        self._subs.setdefault(doc_id, []).append(q)
        return q

    def unsubscribe(self, doc_id: int, q: asyncio.Queue) -> None:
        lst = self._subs.get(doc_id)
        if lst and q in lst:
            lst.remove(q)

    def emit(self, doc_id: int, ev: dict) -> None:
        for q in list(self._subs.get(doc_id, [])):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass

    async def _process(self, doc_id: int) -> None:
        segments = load_segments(self.data_dir, doc_id)
        doc = self.store.get_document(doc_id)
        ps, pc = doc["progress_segment"], doc["progress_chunk"]
        self.store.set_status(doc_id, "running")
        self.emit(doc_id, {"type": "status", "status": "running"})
        gen = self._run(self.cfg, self.store, doc_id, segments,
                        start_segment=ps, start_chunk=pc)
        async for ev in gen:
            self.emit(doc_id, ev)
            if ev.get("type") == "round_end":
                self.store.set_progress(doc_id, ev["segment"], ev["chunk"] + 1)
                if doc_id in self._pause:
                    self._pause.discard(doc_id)
                    self.store.set_status(doc_id, "paused")
                    self.emit(doc_id, {"type": "status", "status": "paused"})
                    await gen.aclose()
                    return

    async def _worker(self) -> None:
        while True:
            doc_id = await self.queue.get()
            try:
                await self._process(doc_id)
            except Exception:
                pass  # le pipeline a déjà émis 'error' et marqué le doc 'failed'
            finally:
                self.queue.task_done()

    def start(self) -> None:
        self._task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_scheduler.py -q` → PASS.
- [ ] **Step 5 : Commit**

```bash
git add nerve/scheduler.py tests/test_scheduler.py
git commit -m "feat(i3): scheduler — file FIFO mono-worker + pub/sub + progression"
```

---

## Task 7 : scheduler.py — pause / resume / reconcile

**Files:** Modify `nerve/scheduler.py` ; Test `tests/test_scheduler.py`

- [ ] **Step 1 : Tests rouges** — ajouter à `tests/test_scheduler.py` :

```python
async def fake_run_two_chunks(cfg, store, doc_id, segments, *, start_segment=0, start_chunk=0, client=None):
    yield {"type": "round_end", "segment": 0, "chunk": 0, "source_file": ""}
    yield {"type": "round_end", "segment": 0, "chunk": 1, "source_file": ""}
    store.finish_document(doc_id)
    yield {"type": "done", "total_facts": 0, "unique_facts": 0, "duplicate_facts": 0}

async def test_pause_stops_at_round_end(tmp_path):
    st = Store(str(tmp_path / "pa.db"), embed_dim=2); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text")
    write_segments(str(tmp_path), doc_id, [("texte", "")])
    sched = Scheduler(load_config(), st, run=fake_run_two_chunks, data_dir=str(tmp_path))
    sched._pause.add(doc_id)                    # pause demandée avant exécution
    sub = sched.subscribe(doc_id)
    sched.start(); sched.enqueue(doc_id)
    try:
        while True:
            ev = await asyncio.wait_for(sub.get(), timeout=2)
            if ev.get("type") == "status" and ev.get("status") == "paused":
                break
    finally:
        await sched.stop()
    doc = st.get_document(doc_id)
    assert doc["status"] == "paused"
    assert doc["progress_chunk"] == 1           # arrêté après le 1er round_end (chunk 0 -> 1)

def test_resume_reenqueues(tmp_path):
    st = Store(str(tmp_path / "rq.db"), embed_dim=2); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text")
    st.set_status(doc_id, "paused")
    sched = Scheduler(load_config(), st, data_dir=str(tmp_path))
    sched.resume(doc_id)
    assert sched.queue.qsize() == 1
    assert st.get_document(doc_id)["status"] == "queued"

def test_pause_not_running_sets_paused(tmp_path):
    st = Store(str(tmp_path / "pn.db"), embed_dim=2); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text")
    st.set_status(doc_id, "queued")
    sched = Scheduler(load_config(), st, data_dir=str(tmp_path))
    sched.pause(doc_id)
    assert st.get_document(doc_id)["status"] == "paused"

def test_reconcile_reenqueues_interrupted(tmp_path):
    st = Store(str(tmp_path / "rc.db"), embed_dim=2); st.init_db()
    s = st.create_set("S")
    d1 = st.create_document(s, "1", "text"); st.set_status(d1, "running")
    d2 = st.create_document(s, "2", "text"); st.finish_document(d2)   # done
    sched = Scheduler(load_config(), st, data_dir=str(tmp_path))
    sched.reconcile()
    assert sched.queue.qsize() == 1
    assert st.get_document(d1)["status"] == "queued"
    assert st.get_document(d2)["status"] == "done"
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_scheduler.py -q` → FAIL (`pause`/`resume`/`reconcile` absents).

- [ ] **Step 3 : Implémenter** — ajouter à `Scheduler` (après `stop`) :

```python
    def reconcile(self) -> None:
        """Au démarrage : ré-enfile les docs interrompus par un crash (running/queued)."""
        for doc_id in self.store.list_resumable():
            self.enqueue(doc_id)

    def pause(self, doc_id: int) -> dict | None:
        doc = self.store.get_document(doc_id)
        if doc is None or doc["status"] in ("done", "failed"):
            return doc
        if doc["status"] == "running":
            self._pause.add(doc_id)            # honoré au prochain round_end
        else:
            self.store.set_status(doc_id, "paused")
        return self.store.get_document(doc_id)

    def resume(self, doc_id: int) -> dict | None:
        self._pause.discard(doc_id)
        self.enqueue(doc_id)
        return self.store.get_document(doc_id)
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_scheduler.py -q` → PASS.
- [ ] **Step 5 : Commit**

```bash
git add nerve/scheduler.py tests/test_scheduler.py
git commit -m "feat(i3): scheduler — pause coopérative / resume / reconcile au startup"
```

---

## Task 8 : api.py — lifespan + enqueue (refactor synchrone → async)

**Files:** Modify `nerve/api.py` ; Test `tests/test_api.py`

- [ ] **Step 1 : Réécrire les tests API pour le modèle async** — dans `tests/test_api.py`, **remplacer** `test_create_document_and_get_facts`, `test_create_document_from_url`, `test_upload_zip` (qui supposaient une extraction synchrone) par les versions ci-dessous, et **garder** `test_get_facts_unknown_document_returns_404`, `test_create_document_requires_text_or_url`, `test_create_document_url_transcode_failure_422`, `test_upload_unreadable_file_fails_loud` (inchangés). Ajouter `import os, json` en tête si absent.

```python
def test_create_document_enqueues(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")
    import importlib, nerve.api as api
    importlib.reload(api)
    client = TestClient(api.app)                 # sans 'with' -> worker non démarré
    r = client.post("/api/documents", json={"title": "t", "text": "le chat dort"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    doc_id = body["document_id"]
    assert client.get(f"/api/documents/{doc_id}").json()["status"] == "queued"
    seg = os.path.join(str(tmp_path), "inputs", str(doc_id), "segments.jsonl")
    assert os.path.exists(seg)

def test_create_document_from_url_enqueues(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")
    import importlib, nerve.api as api
    importlib.reload(api)
    monkeypatch.setattr(api, "transcode_url", fake_transcode)
    client = TestClient(api.app)
    r = client.post("/api/documents", json={"url": "https://ex.com/a"})
    assert r.status_code == 200 and r.json()["status"] == "queued"
    doc_id = r.json()["document_id"]
    doc = client.get(f"/api/documents/{doc_id}").json()
    assert doc["source_kind"] == "url" and doc["source_ref"] == "https://ex.com/a"
    assert json.loads(doc["params_json"])["dedup_field"] == api.cfg.dedup_field

def test_upload_zip_enqueues(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")
    import importlib, nerve.api as api
    importlib.reload(api)
    client = TestClient(api.app)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.txt", "le chat dort")
        z.writestr("bad.txt", "")
    r = client.post("/api/documents/upload",
                    files={"file": ("c.zip", buf.getvalue(), "application/zip")},
                    data={"set_name": "S"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued" and body["skipped"] == ["bad.txt"]
    doc = client.get(f"/api/documents/{body['document_id']}").json()
    assert doc["source_kind"] == "file" and doc["source_ref"] == "c.zip"
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_api.py -q` → FAIL (réponse n'a pas `status:queued` ; pas de route `GET /api/documents/{id}` ; extraction encore synchrone).

- [ ] **Step 3 : Implémenter** — dans `nerve/api.py` :

Remplacer les imports en tête + l'init par :

```python
import os
import json
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Form, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from nerve.config import load_config
from nerve.store import Store
from nerve.scheduler import Scheduler, write_segments
from nerve.transcode import transcode_url
from nerve.ingest import ingest_upload, IngestError

cfg = load_config()
store = Store(cfg.db_path, embed_dim=cfg.embed_dim)
store.init_db()
scheduler = Scheduler(cfg, store)

@asynccontextmanager
async def lifespan(app):
    scheduler.start()
    scheduler.reconcile()
    yield
    await scheduler.stop()

app = FastAPI(title="nerve", lifespan=lifespan)
WEB = os.path.join(os.path.dirname(__file__), "web")
```

(Supprimer l'ancien `from nerve.pipeline import run_extraction` — plus appelé directement ici.)

Remplacer le handler `create_document` par :

```python
@app.post("/api/documents")
async def create_document(body: CreateDoc):
    set_id = body.set_id or store.create_set(body.set_name)
    if body.url:
        try:
            md, transcoded_title = await transcode_url(cfg, body.url)
        except RuntimeError as e:
            raise HTTPException(status_code=422, detail=str(e))
        title = body.title if body.title != "Sans titre" else (transcoded_title or body.url)
        doc_id = store.create_document(set_id, title, "url", source_ref=body.url,
                                       params={"dedup_field": cfg.dedup_field})
        segments = [(md, "")]
    elif body.text:
        doc_id = store.create_document(set_id, body.title, "text",
                                       params={"dedup_field": cfg.dedup_field})
        segments = [(body.text, "")]
    else:
        raise HTTPException(status_code=400, detail="text ou url requis")
    write_segments(cfg.data_dir, doc_id, segments)
    scheduler.enqueue(doc_id)
    return {"document_id": doc_id, "status": "queued"}
```

Remplacer le handler `upload_document` par :

```python
@app.post("/api/documents/upload")
async def upload_document(file: UploadFile = File(...), set_id: int | None = Form(None),
                          set_name: str = Form("Défaut"), title: str = Form("")):
    raw = await file.read()
    name = file.filename or "upload"
    sid = set_id or store.create_set(set_name)
    doc_id = store.create_document(sid, title or name, "file", source_ref=name,
                                   params={"dedup_field": cfg.dedup_field})
    dest = os.path.join(cfg.data_dir, "inputs", str(doc_id))
    try:
        segments, skipped = ingest_upload(name, raw, dest)
    except IngestError as e:
        store.finish_document(doc_id, error=str(e))
        raise HTTPException(status_code=422, detail=str(e))
    write_segments(cfg.data_dir, doc_id, segments)
    scheduler.enqueue(doc_id)
    return {"document_id": doc_id, "status": "queued", "skipped": skipped}
```

Ajouter la route métadonnées (utilisée par les tests de cette tâche), après `upload_document` :

```python
@app.get("/api/documents/{doc_id}")
def get_document(doc_id: int):
    doc = store.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document introuvable")
    return doc
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_api.py -q` → PASS.

- [ ] **Step 5 : Commit**

```bash
git add nerve/api.py tests/test_api.py
git commit -m "feat(i3): api — lifespan(scheduler) + enqueue (POST documents/upload async) + GET /documents/{id}"
```

---

## Task 9 : api.py — endpoints pause / resume

**Files:** Modify `nerve/api.py` ; Test `tests/test_api.py`

- [ ] **Step 1 : Tests rouges** — ajouter à `tests/test_api.py` :

```python
def test_pause_resume_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")
    import importlib, nerve.api as api
    importlib.reload(api)
    client = TestClient(api.app)
    doc_id = api.store.create_document(api.store.create_set("S"), "d", "text")
    api.store.set_status(doc_id, "queued")
    assert client.post(f"/api/documents/{doc_id}/pause").json()["status"] == "paused"
    assert client.post(f"/api/documents/{doc_id}/resume").json()["status"] == "queued"
    assert client.post("/api/documents/9999/pause").status_code == 404
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_api.py::test_pause_resume_routes -q` → FAIL (404/405 : routes absentes).

- [ ] **Step 3 : Implémenter** — ajouter à `nerve/api.py` (après `get_document`) :

```python
@app.post("/api/documents/{doc_id}/pause")
def pause_document(doc_id: int):
    doc = scheduler.pause(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document introuvable")
    return doc

@app.post("/api/documents/{doc_id}/resume")
def resume_document(doc_id: int):
    if store.get_document(doc_id) is None:
        raise HTTPException(status_code=404, detail="Document introuvable")
    return scheduler.resume(doc_id)
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_api.py -q` → PASS.
- [ ] **Step 5 : Commit**

```bash
git add nerve/api.py tests/test_api.py
git commit -m "feat(i3): api — endpoints pause/resume"
```

---

## Task 10 : api.py — SSE `/events`

**Files:** Modify `nerve/api.py` ; Test `tests/test_api.py`

- [ ] **Step 1 : Test rouge** — ajouter à `tests/test_api.py` :

```python
def test_sse_replay_for_done_document(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")
    import importlib, nerve.api as api
    importlib.reload(api)
    doc_id = api.store.create_document(api.store.create_set("S"), "d", "text")
    api.store.add_fact(doc_id, {"subject": "A", "predicate": "r", "object": "B"})
    api.store.finish_document(doc_id)                 # statut done -> le flux se termine seul
    client = TestClient(api.app)
    with client.stream("GET", f"/api/documents/{doc_id}/events") as r:
        body = "".join(r.iter_text())
    assert '"type": "replay"' in body
    assert '"type": "status"' in body
    assert '"A"' in body                              # le fait rejoué est présent
    assert client.get("/api/documents/9999/events").status_code == 404
```

- [ ] **Step 2 : Échec** — `uv run pytest tests/test_api.py::test_sse_replay_for_done_document -q` → FAIL (route `/events` absente).

- [ ] **Step 3 : Implémenter** — ajouter à `nerve/api.py` (après `resume_document`) :

```python
@app.get("/api/documents/{doc_id}/events")
async def document_events(doc_id: int):
    if store.get_document(doc_id) is None:
        raise HTTPException(status_code=404, detail="Document introuvable")

    async def gen():
        q = scheduler.subscribe(doc_id)
        try:
            yield f"data: {json.dumps({'type': 'replay', 'facts': store.get_facts(doc_id)})}\n\n"
            doc = store.get_document(doc_id)
            yield f"data: {json.dumps({'type': 'status', 'status': doc['status'], 'total_facts': doc['total_facts'], 'unique_facts': doc['unique_facts'], 'duplicate_facts': doc['duplicate_facts']})}\n\n"
            if doc["status"] in ("done", "failed"):
                return
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev)}\n\n"
                    if ev.get("type") in ("done", "error"):
                        break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            scheduler.unsubscribe(doc_id, q)

    return StreamingResponse(gen(), media_type="text/event-stream")
```

- [ ] **Step 4 : Succès** — `uv run pytest tests/test_api.py -q` → PASS.
- [ ] **Step 5 : Commit**

```bash
git add nerve/api.py tests/test_api.py
git commit -m "feat(i3): api — flux SSE /events (replay + live + keepalive)"
```

---

## Task 11 : web/index.html — consommation SSE

**Files:** Modify `nerve/web/index.html` (pas de test auto — vérifié au smoke)

- [ ] **Step 1 : Implémenter** — remplacer le bloc `document.getElementById("go").addEventListener(...)` (lignes ~50-58) par :

```javascript
let nodes=new Map(), links=[];
function addFact(f){
  const s=f.subject_canonical||f.subject, o=f.object_canonical||f.object;
  if(!s||!o) return;
  nodes.set(s,{id:s}); nodes.set(o,{id:o});
  links.push({source:s,target:o,predicate:f.predicate});
}
function redraw(){ G.graphData({nodes:[...nodes.values()],links}); }

document.getElementById("go").addEventListener("click", async ()=>{
  const text=document.getElementById("txt").value.trim(); if(!text) return;
  nodes=new Map(); links=[]; redraw();
  const r=await fetch("/api/documents",{method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({title:"Coller",text})});
  const {document_id}=await r.json();
  const es=new EventSource(`/api/documents/${document_id}/events`);
  es.onmessage=(e)=>{
    const m=JSON.parse(e.data);
    if(m.type==="replay"){ m.facts.forEach(addFact); redraw(); }
    else if(m.type==="fact" && !m.is_duplicate){ addFact(m.fact); redraw(); }
    else if(m.type==="done"||m.type==="error"){ es.close(); }
  };
  es.onerror=()=>es.close();
});
```

(Conserver `escapeHtml`, `render` n'est plus utilisé — supprimer la fonction `render` devenue morte si présente.)

- [ ] **Step 2 : Vérifier la suite complète**

Run: `uv run pytest -q`
Expected: PASS (suite I-1 + I-2 + I-3).

- [ ] **Step 3 : Commit**

```bash
git add nerve/web/index.html
git commit -m "feat(i3): front — consommation SSE (faits en direct)"
```

---

## Vérifications réelles (smoke — Ollama requis)

1. `rm -f data/nerve.db*` (schéma : colonnes `progress_*`).
2. `uv run nerve` → coller un texte : la visu se remplit **en direct** (SSE) ; `GET /api/documents/<id>` passe `queued`→`running`→`done`.
3. **Pause/reprise** : coller un gros texte (plusieurs chunks), `POST …/pause` en cours → statut `paused`, `progress_chunk` figé ; `POST …/resume` → reprend sans re-extraire les chunks faits.
4. **Reprise après crash** : `Ctrl-C` le serveur en cours d'extraction, relancer → le doc reprend automatiquement (reconcile) au dernier chunk persisté.
5. **Concurrence** : ouvrir `GET …/events` d'un doc pendant l'extraction d'un autre → pas de blocage (WAL).

---

## Self-Review (couverture spec → tâches)

- Spec §4 scheduler (file, pub/sub, worker, pause coopérative, reconcile) → **T5, T6, T7**.
- Spec §5 persistance segments + reprise (segments.jsonl, progress, preload) → **T5** (io), **T1** (colonnes), **T2** (load), **T3** (preload), **T4** (reprise pipeline).
- Spec §6 pipeline (start_segment/start_chunk, round_end{segment,chunk}, pipeline « pur ») → **T4** (la progression/pause restent au worker T6).
- Spec §7 store (WAL, statuts, progression, load) → **T1, T2**.
- Spec §8 api (lifespan, enqueue, pause/resume/events SSE, GET /{id}) → **T8, T9, T10**.
- Spec §9 front SSE → **T11**.
- Spec §10 tests → couverts T1-T10 ; bout-en-bout réel = smoke (§11).
- Hors périmètre (DELETE, métriques tps, rendu soigné, préemption/backfill) : absents ✔.

Cohérence des signatures : `run_extraction(cfg, store, doc_id, segments, *, start_segment=0, start_chunk=0, client=None)` (T4) appelée par `Scheduler._process` avec `start_segment=ps, start_chunk=pc` (T6) ; `write_segments(data_dir, doc_id, segments)` / `load_segments(data_dir, doc_id)` (T5) utilisées par T6/T8 ; `Scheduler(cfg, store, *, run=, data_dir=)` (T6) ; `FactDeduper.preload(items)` / `EntityResolver.preload(rows)` (T3) appelées par run_extraction (T4) avec `store.load_fact_vectors`/`store.load_entities` (T2). Pas de placeholder.

**Note de risque (à lever à l'implémentation)** : test SSE sous `TestClient` repose sur un doc déjà `done` (le générateur sort sans boucle live) — le live (worker actif) est couvert par les tests scheduler T6/T7 et le smoke, pas via TestClient (fragile en event-loop de test).
