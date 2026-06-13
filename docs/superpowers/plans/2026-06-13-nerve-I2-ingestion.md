# Plan 2 / I-2 · Ingestion (URL / fichiers / zip) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permettre de nourrir nerve via URL (transcodage markdown enfichable) et fichiers/zip (pdf, docx, html, texte), avec `source_file` par fait pour les zip.

**Architecture:** Deux nouveaux modules — `transcode.py` (chaîne ordonnée de backends URL→markdown, repli, fail-loud si tous échouent) et `ingest.py` (lecture fichiers + parcours zip sûr, résilient sur le lot). `run_extraction` passe d'un `text` unique à une liste de **segments** `(text, source_file)` partageant `EntityResolver`/`FactDeduper` (dedup cross-fichier). L'API gagne le champ `url` sur `POST /api/documents` et un endpoint `POST /api/documents/upload`.

**Tech Stack:** Python 3.11+, FastAPI, httpx, trafilatura (déjà), pypdf (déjà), **python-docx (nouveau)**, zipfile (stdlib), pytest (asyncio auto).

**Réf. spec :** `docs/superpowers/specs/2026-06-13-nerve-I2-ingestion-design.md`.

---

## File Structure

- **Create** `nerve/transcode.py` — backends `_trafilatura`/`_puremd`/`_jina`, orchestrateur `transcode_url`, `_first_title`, `BACKENDS`.
- **Create** `nerve/ingest.py` — `IngestError`, `read_file` (+ `_read_pdf`/`_read_docx`/`_read_html`), `TEXT_EXTS`, `ingest_upload` (+ `_ingest_zip`).
- **Create** `tests/test_transcode.py`, `tests/test_ingest.py`.
- **Modify** `nerve/config.py` — `url_transcoders`, `puremd_token`, `jina_key`.
- **Modify** `nerve/pipeline.py` — `run_extraction(..., segments)` + `source_file`.
- **Modify** `nerve/api.py` — `url` sur `/documents`, `/documents/upload`, correctif M-2.
- **Modify** `pyproject.toml` — dép `python-docx`.
- **Modify** `tests/test_config.py`, `tests/test_pipeline.py`, `tests/test_api.py`.

Conventions reprises de I-1 : tests sans réseau (mocks `monkeypatch`), `TestClient` + `importlib.reload(api)` après `setenv`, `asyncio_mode=auto` (tests `async def` directs).

---

## Task 1 : Config — chaîne de transcodeurs + clés

**Files:**
- Modify: `nerve/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1 : Écrire les tests rouges**

Ajouter à `tests/test_config.py` :

```python
def test_transcoders_default(monkeypatch):
    monkeypatch.delenv("URL_TRANSCODERS", raising=False)
    c = cfgmod.load_config()
    assert c.url_transcoders == ("trafilatura",)
    assert c.puremd_token == ""
    assert c.jina_key == ""

def test_transcoders_override(monkeypatch):
    monkeypatch.setenv("URL_TRANSCODERS", "trafilatura, puremd , jina")
    monkeypatch.setenv("PUREMD_API_TOKEN", "p-tok")
    monkeypatch.setenv("JINA_API_KEY", "j-key")
    c = cfgmod.load_config()
    assert c.url_transcoders == ("trafilatura", "puremd", "jina")
    assert c.puremd_token == "p-tok"
    assert c.jina_key == "j-key"
```

- [ ] **Step 2 : Vérifier l'échec**

Run: `uv run pytest tests/test_config.py -q`
Expected: FAIL (`AttributeError: 'Config' object has no attribute 'url_transcoders'`).

- [ ] **Step 3 : Implémenter**

Dans `nerve/config.py`, ajouter les champs au dataclass `Config` (après `dedup_field: str`) :

```python
    url_transcoders: tuple[str, ...]
    puremd_token: str
    jina_key: str
```

Et dans `load_config()`, à l'intérieur du `return Config(...)`, ajouter :

```python
        url_transcoders=tuple(
            s.strip() for s in os.environ.get("URL_TRANSCODERS", "trafilatura").split(",")
            if s.strip()
        ),
        puremd_token=os.environ.get("PUREMD_API_TOKEN", ""),
        jina_key=os.environ.get("JINA_API_KEY", ""),
```

- [ ] **Step 4 : Vérifier le succès**

Run: `uv run pytest tests/test_config.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5 : Commit**

```bash
git add nerve/config.py tests/test_config.py
git commit -m "feat(i2): config chaîne de transcodeurs URL + clés puremd/jina"
```

---

## Task 2 : transcode.py — `_first_title` + backend trafilatura

**Files:**
- Create: `nerve/transcode.py`
- Test: `tests/test_transcode.py`

- [ ] **Step 1 : Écrire les tests rouges**

Créer `tests/test_transcode.py` :

```python
import nerve.transcode as tc
from nerve.config import load_config

def test_first_title_from_title_line():
    assert tc._first_title("Title: Mon Doc\n\n# autre\ncorps") == "Mon Doc"

def test_first_title_from_heading():
    assert tc._first_title("# Mon Titre\n\ncorps") == "Mon Titre"

def test_first_title_absent():
    assert tc._first_title("corps sans titre\nligne 2") == ""

async def test_trafilatura_backend(monkeypatch):
    monkeypatch.setattr(tc.trafilatura, "fetch_url", lambda u: "<html>x</html>")
    monkeypatch.setattr(tc.trafilatura, "extract", lambda html, **k: "# T\n\ncorps")
    class _M:
        title = "T"
    monkeypatch.setattr(tc.trafilatura, "extract_metadata", lambda html: _M())
    md, title = await tc._trafilatura("http://x", load_config(), client=None)
    assert md == "# T\n\ncorps"
    assert title == "T"

async def test_trafilatura_backend_empty_returns_none(monkeypatch):
    monkeypatch.setattr(tc.trafilatura, "fetch_url", lambda u: None)
    assert await tc._trafilatura("http://x", load_config(), client=None) is None
```

- [ ] **Step 2 : Vérifier l'échec**

Run: `uv run pytest tests/test_transcode.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'nerve.transcode'`).

- [ ] **Step 3 : Implémenter**

Créer `nerve/transcode.py` :

```python
# nerve/transcode.py
import asyncio
import httpx
import trafilatura
from nerve.config import Config


def _first_title(md: str) -> str:
    """Titre = 1re ligne « Title: … » ou « # … » dans les 5 premières lignes."""
    for line in md.strip().split("\n")[:5]:
        if line.startswith("Title:"):
            return line[len("Title:"):].strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""


async def _trafilatura(url: str, cfg: Config, *, client) -> tuple[str, str] | None:
    """Backend local (défaut) : n'utilise pas `client` ni de clé. fetch_url/extract
    sont synchrones (urllib3) -> exécutés hors boucle via to_thread."""
    downloaded = await asyncio.to_thread(trafilatura.fetch_url, url)
    if not downloaded:
        return None
    md = await asyncio.to_thread(trafilatura.extract, downloaded, output_format="markdown")
    if not md or not md.strip():
        return None
    meta = trafilatura.extract_metadata(downloaded)
    title = (meta.title or "") if meta else ""
    return md, title
```

- [ ] **Step 4 : Vérifier le succès**

Run: `uv run pytest tests/test_transcode.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5 : Commit**

```bash
git add nerve/transcode.py tests/test_transcode.py
git commit -m "feat(i2): transcode — _first_title + backend trafilatura (local, défaut)"
```

---

## Task 3 : transcode.py — backends distants jina & puremd

**Files:**
- Modify: `nerve/transcode.py`
- Test: `tests/test_transcode.py`

- [ ] **Step 1 : Écrire les tests rouges**

Ajouter à `tests/test_transcode.py` (en tête : `import httpx`) :

```python
import httpx

async def test_jina_backend(monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", "K")
    cfg = load_config()
    seen = {}
    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, text="Title: Foo\n\n# Foo\n\ncorps")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    md, title = await tc._jina("https://ex.com/a", cfg, client=client)
    await client.aclose()
    assert seen["url"].startswith("https://r.jina.ai/")
    assert "ex.com" in seen["url"]
    assert seen["auth"] == "Bearer K"
    assert title == "Foo"

async def test_jina_skips_without_key(monkeypatch):
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    assert await tc._jina("http://x", load_config(), client=None) is None

async def test_puremd_backend(monkeypatch):
    monkeypatch.setenv("PUREMD_API_TOKEN", "T")
    cfg = load_config()
    seen = {}
    def handler(request):
        seen["url"] = str(request.url)
        seen["tok"] = request.headers.get("x-puremd-api-token")
        return httpx.Response(200, text="# Bar\n\ncorps")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    md, title = await tc._puremd("https://ex.com/a", cfg, client=client)
    await client.aclose()
    assert seen["url"].startswith("https://pure.md/")
    assert seen["tok"] == "T"
    assert title == "Bar"

async def test_puremd_skips_without_key(monkeypatch):
    monkeypatch.delenv("PUREMD_API_TOKEN", raising=False)
    assert await tc._puremd("http://x", load_config(), client=None) is None
```

- [ ] **Step 2 : Vérifier l'échec**

Run: `uv run pytest tests/test_transcode.py -q`
Expected: FAIL (`AttributeError: module 'nerve.transcode' has no attribute '_jina'`).

- [ ] **Step 3 : Implémenter**

Ajouter à `nerve/transcode.py` (après `_trafilatura`) :

```python
async def _jina(url: str, cfg: Config, *, client) -> tuple[str, str] | None:
    """Jina Reader (r.jina.ai). Activé si JINA_API_KEY est défini."""
    if not cfg.jina_key:
        return None
    r = await client.get(
        f"https://r.jina.ai/{url}",
        headers={"Authorization": f"Bearer {cfg.jina_key}", "Accept": "text/markdown"},
    )
    r.raise_for_status()
    text = r.text
    if not text.strip():
        return None
    return text, _first_title(text)


async def _puremd(url: str, cfg: Config, *, client) -> tuple[str, str] | None:
    """Pure.md (préfixe d'URL). Activé si PUREMD_API_TOKEN est défini."""
    if not cfg.puremd_token:
        return None
    r = await client.get(
        f"https://pure.md/{url}",
        headers={"x-puremd-api-token": cfg.puremd_token},
    )
    r.raise_for_status()
    text = r.text
    if not text.strip():
        return None
    return text, _first_title(text)
```

- [ ] **Step 4 : Vérifier le succès**

Run: `uv run pytest tests/test_transcode.py -q`
Expected: PASS (9 tests).

- [ ] **Step 5 : Commit**

```bash
git add nerve/transcode.py tests/test_transcode.py
git commit -m "feat(i2): transcode — backends distants jina + puremd (clé requise)"
```

---

## Task 4 : transcode.py — orchestrateur `transcode_url` + `BACKENDS`

**Files:**
- Modify: `nerve/transcode.py`
- Test: `tests/test_transcode.py`

- [ ] **Step 1 : Écrire les tests rouges**

Ajouter à `tests/test_transcode.py` (en tête : `import pytest`) :

```python
import pytest

async def test_chain_uses_first_non_empty(monkeypatch):
    async def empty(url, cfg, *, client):
        return None
    async def good(url, cfg, *, client):
        return ("md ok", "T")
    monkeypatch.setattr(tc, "BACKENDS", {"a": empty, "b": good})
    monkeypatch.setenv("URL_TRANSCODERS", "a,b")
    md, title = await tc.transcode_url(load_config(), "http://x")
    assert md == "md ok"
    assert title == "T"

async def test_chain_skips_failing_backend(monkeypatch):
    async def boom(url, cfg, *, client):
        raise RuntimeError("backend HS")
    async def good(url, cfg, *, client):
        return ("ok", "")
    monkeypatch.setattr(tc, "BACKENDS", {"a": boom, "b": good})
    monkeypatch.setenv("URL_TRANSCODERS", "a,b")
    md, _ = await tc.transcode_url(load_config(), "http://x")
    assert md == "ok"

async def test_all_backends_fail_raises(monkeypatch):
    async def boom(url, cfg, *, client):
        raise RuntimeError("nope")
    async def empty(url, cfg, *, client):
        return None
    monkeypatch.setattr(tc, "BACKENDS", {"a": boom, "b": empty})
    monkeypatch.setenv("URL_TRANSCODERS", "a,b")
    with pytest.raises(RuntimeError):
        await tc.transcode_url(load_config(), "http://x")
```

- [ ] **Step 2 : Vérifier l'échec**

Run: `uv run pytest tests/test_transcode.py -q`
Expected: FAIL (`AttributeError: module 'nerve.transcode' has no attribute 'transcode_url'`).

- [ ] **Step 3 : Implémenter**

Ajouter à la fin de `nerve/transcode.py` :

```python
BACKENDS = {"trafilatura": _trafilatura, "puremd": _puremd, "jina": _jina}


async def transcode_url(cfg: Config, url: str, *, client=None) -> tuple[str, str]:
    """Parcourt cfg.url_transcoders dans l'ordre, retient le 1er markdown non vide.
    Repli sur échec/vide ; fail-loud si TOUS les backends échouent."""
    owns = client is None
    client = client or httpx.AsyncClient(timeout=30)
    errors = []
    try:
        for name in cfg.url_transcoders:
            backend = BACKENDS.get(name)
            if backend is None:
                errors.append(f"{name}: inconnu")
                continue
            try:
                res = await backend(url, cfg, client=client)
            except Exception as e:
                errors.append(f"{name}: {e}")
                continue
            if res and res[0].strip():
                return res
            errors.append(f"{name}: vide")
        raise RuntimeError(f"Transcodage échoué pour {url} ({'; '.join(errors)})")
    finally:
        if owns:
            await client.aclose()
```

- [ ] **Step 4 : Vérifier le succès**

Run: `uv run pytest tests/test_transcode.py -q`
Expected: PASS (12 tests).

- [ ] **Step 5 : Commit**

```bash
git add nerve/transcode.py tests/test_transcode.py
git commit -m "feat(i2): transcode — chaîne ordonnée + repli + fail-loud"
```

---

## Task 5 : Dépendance python-docx

**Files:**
- Modify: `pyproject.toml` (+ `uv.lock`)

- [ ] **Step 1 : Ajouter la dépendance**

Run: `uv add "python-docx>=1.1"`
(modifie `pyproject.toml` et `uv.lock`, installe le paquet `python-docx` — import `docx`.)

- [ ] **Step 2 : Vérifier l'import**

Run: `uv run python -c "from docx import Document; print('ok')"`
Expected: `ok`.

- [ ] **Step 3 : Vérifier la non-régression**

Run: `uv run pytest -q`
Expected: PASS (suite actuelle inchangée).

- [ ] **Step 4 : Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat(i2): dépendance python-docx (lecture .docx)"
```

---

## Task 6 : ingest.py — `read_file`

**Files:**
- Create: `nerve/ingest.py`
- Test: `tests/test_ingest.py`

- [ ] **Step 1 : Écrire les tests rouges**

Créer `tests/test_ingest.py` :

```python
import pytest
import nerve.ingest as ing
from nerve.ingest import IngestError

def test_read_txt(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("contenu texte", encoding="utf-8")
    assert ing.read_file(str(p), "a.txt") == "contenu texte"

def test_read_empty_raises(tmp_path):
    p = tmp_path / "v.txt"
    p.write_text("   \n  ", encoding="utf-8")
    with pytest.raises(IngestError):
        ing.read_file(str(p), "v.txt")

def test_read_docx(tmp_path):
    from docx import Document
    d = Document()
    d.add_paragraph("Bonjour")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text = "A"
    t.rows[0].cells[1].text = "B"
    p = tmp_path / "doc.docx"
    d.save(str(p))
    out = ing.read_file(str(p), "doc.docx")
    assert "Bonjour" in out
    assert "A" in out and "B" in out

def test_read_pdf_via_pypdf(tmp_path, monkeypatch):
    class _Page:
        def extract_text(self):
            return "page un"
    class _Reader:
        def __init__(self, path):
            self.pages = [_Page()]
    monkeypatch.setattr(ing, "PdfReader", _Reader)
    p = tmp_path / "x.pdf"
    p.write_bytes(b"%PDF-1.4 factice")
    assert ing.read_file(str(p), "x.pdf") == "page un"

def test_read_pdf_corrupt_raises(tmp_path, monkeypatch):
    def _boom(path):
        raise ValueError("pdf cassé")
    monkeypatch.setattr(ing, "PdfReader", _boom)
    p = tmp_path / "bad.pdf"
    p.write_bytes(b"pas un pdf")
    with pytest.raises(IngestError):
        ing.read_file(str(p), "bad.pdf")

def test_read_html_via_trafilatura(tmp_path, monkeypatch):
    monkeypatch.setattr(ing.trafilatura, "extract", lambda raw, **k: "# T\n\ncorps")
    p = tmp_path / "page.html"
    p.write_text("<html><body><p>x</p></body></html>", encoding="utf-8")
    assert "corps" in ing.read_file(str(p), "page.html")
```

- [ ] **Step 2 : Vérifier l'échec**

Run: `uv run pytest tests/test_ingest.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'nerve.ingest'`).

- [ ] **Step 3 : Implémenter**

Créer `nerve/ingest.py` :

```python
# nerve/ingest.py
import os
import trafilatura
from pypdf import PdfReader
from docx import Document


class IngestError(Exception):
    """Lecture impossible (fichier vide, illisible, format cassé)."""


TEXT_EXTS = {".txt", ".md", ".markdown", ".text", ".rst", ".json", ".jsonl",
             ".csv", ".tsv", ".log", ".html", ".htm", ".pdf", ".docx",
             ".xml", ".yaml", ".yml", ".py", ".js", ".ts"}


def _read_pdf(path: str) -> str:
    try:
        reader = PdfReader(path)
        return "\n\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as e:
        raise IngestError(f"PDF illisible : {e}")


def _read_docx(path: str) -> str:
    try:
        doc = Document(path)
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                parts.append("\t".join(c.text for c in row.cells))
        return "\n".join(parts)
    except Exception as e:
        raise IngestError(f"docx illisible : {e}")


def _read_html(path: str) -> str:
    raw = open(path, encoding="utf-8", errors="ignore").read()
    return trafilatura.extract(raw, output_format="markdown") or ""


def read_file(path: str, name: str) -> str:
    """Extrait le texte d'un fichier. Lève IngestError si vide/illisible."""
    ext = os.path.splitext(name)[1].lower()
    if ext == ".pdf":
        text = _read_pdf(path)
    elif ext == ".docx":
        text = _read_docx(path)
    elif ext in (".html", ".htm"):
        text = _read_html(path)
    else:
        text = open(path, encoding="utf-8", errors="ignore").read()
    if not text or not text.strip():
        raise IngestError(f"Contenu vide/illisible : {name}")
    return text
```

- [ ] **Step 4 : Vérifier le succès**

Run: `uv run pytest tests/test_ingest.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5 : Commit**

```bash
git add nerve/ingest.py tests/test_ingest.py
git commit -m "feat(i2): ingest read_file (pdf/docx/html/texte, IngestError fail-loud)"
```

---

## Task 7 : ingest.py — `ingest_upload` + parcours zip

**Files:**
- Modify: `nerve/ingest.py`
- Test: `tests/test_ingest.py`

- [ ] **Step 1 : Écrire les tests rouges**

Ajouter à `tests/test_ingest.py` (en tête : `import io`, `import zipfile`) :

```python
import io
import zipfile

def _make_zip(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in entries.items():
            z.writestr(name, content)
    return buf.getvalue()

def test_ingest_zip_resilient(tmp_path):
    raw = _make_zip({"a.txt": "contenu A", "empty.txt": "", "../evil.txt": "x"})
    segments, skipped = ing.ingest_upload("c.zip", raw, str(tmp_path / "dest"))
    assert segments == [("contenu A", "a.txt")]      # empty ignoré, evil non extrait
    assert skipped == ["empty.txt"]

def test_ingest_zip_all_unreadable_raises(tmp_path):
    raw = _make_zip({"empty.txt": "   "})
    with pytest.raises(IngestError):
        ing.ingest_upload("c.zip", raw, str(tmp_path / "dest"))

def test_ingest_single_file(tmp_path):
    segments, skipped = ing.ingest_upload("note.txt", b"un seul fichier", str(tmp_path / "d2"))
    assert segments == [("un seul fichier", "")]
    assert skipped == []

def test_ingest_single_file_empty_raises(tmp_path):
    with pytest.raises(IngestError):
        ing.ingest_upload("vide.txt", b"   ", str(tmp_path / "d3"))
```

- [ ] **Step 2 : Vérifier l'échec**

Run: `uv run pytest tests/test_ingest.py -q`
Expected: FAIL (`AttributeError: module 'nerve.ingest' has no attribute 'ingest_upload'`).

- [ ] **Step 3 : Implémenter**

Ajouter à `nerve/ingest.py` (compléter les imports en tête : `import shutil`, `import zipfile`) :

```python
def ingest_upload(filename: str, raw: bytes, dest_dir: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Ingestion d'un upload. Conserve le brut sous dest_dir.
    Retourne (segments[(text, source_file)], skipped[noms ignorés pour illisibilité]).
    Zip : résilient (un fichier illisible -> skipped). Mono-fichier ou zip 100 %
    illisible -> IngestError (fail-loud)."""
    os.makedirs(dest_dir, exist_ok=True)
    raw_path = os.path.join(dest_dir, os.path.basename(filename))
    with open(raw_path, "wb") as f:
        f.write(raw)
    if filename.lower().endswith(".zip"):
        return _ingest_zip(raw_path, dest_dir)
    text = read_file(raw_path, filename)        # IngestError remonte (fail-loud)
    return [(text, "")], []


def _ingest_zip(zip_path: str, dest_dir: str) -> tuple[list[tuple[str, str]], list[str]]:
    segments: list[tuple[str, str]] = []
    skipped: list[str] = []
    had_supported = False
    root = os.path.abspath(dest_dir) + os.sep
    with zipfile.ZipFile(zip_path) as zf:
        for info in sorted(zf.infolist(), key=lambda i: i.filename):
            if info.is_dir():
                continue
            name = info.filename
            base = os.path.basename(name)
            if not base or base.startswith(".") or "__MACOSX" in name:
                continue
            if os.path.splitext(base)[1].lower() not in TEXT_EXTS:
                continue
            target = os.path.join(dest_dir, name)
            if not os.path.abspath(target).startswith(root):   # anti path-traversal
                continue
            had_supported = True
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            try:
                text = read_file(target, name)
            except IngestError:
                skipped.append(name)
                continue
            segments.append((text, name))
    if had_supported and not segments:
        raise IngestError(f"Aucun fichier lisible dans {os.path.basename(zip_path)}")
    return segments, skipped
```

- [ ] **Step 4 : Vérifier le succès**

Run: `uv run pytest tests/test_ingest.py -q`
Expected: PASS (10 tests).

- [ ] **Step 5 : Commit**

```bash
git add nerve/ingest.py tests/test_ingest.py
git commit -m "feat(i2): ingest_upload — zip sûr + résilience de lot + mono-fichier fail-loud"
```

---

## Task 8 : pipeline.py — segments `(text, source_file)`

**Files:**
- Modify: `nerve/pipeline.py`
- Modify: `nerve/api.py` (mise à jour de l'appel existant pour garder la suite verte)
- Test: `tests/test_pipeline.py`

- [ ] **Step 1 : Adapter le test existant + ajouter le test multi-segments**

Dans `tests/test_pipeline.py`, **remplacer** la ligne d'appel du test existant :

```python
    events = [e async for e in pipe.run_extraction(cfg, st, doc_id, "un texte")]
```

par :

```python
    events = [e async for e in pipe.run_extraction(cfg, st, doc_id, [("un texte", "")])]
```

Puis **ajouter** à `tests/test_pipeline.py` :

```python
async def fake_stream_one(cfg, messages, **kw):
    # le MÊME fait pour chaque segment -> le 2e (autre fichier) est un doublon
    yield '[{"subject":"Cluny","predicate":"a_pour","object":"Scriptorium"}]'

async def test_run_extraction_dedups_across_segments(tmp_path, monkeypatch):
    monkeypatch.setattr(pipe, "stream_chat", fake_stream_one)
    monkeypatch.setattr(pipe, "embed", fake_embed)      # réutilise la table de fake_embed
    st = Store(str(tmp_path / "seg.db"), embed_dim=3); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "file")
    cfg = load_config()
    segs = [("texte A", "a.txt"), ("texte B", "b.txt")]
    events = [e async for e in pipe.run_extraction(cfg, st, doc_id, segs)]
    assert events[-1]["type"] == "done"
    doc = st.get_document(doc_id)
    assert doc["total_facts"] == 2
    assert doc["unique_facts"] == 1
    assert doc["duplicate_facts"] == 1               # dedup traverse les 2 fichiers
    facts = st.get_facts(doc_id)                     # non-dup
    assert len(facts) == 1
    assert facts[0]["source_file"] == "a.txt"        # provenance du 1er segment
```

- [ ] **Step 2 : Vérifier l'échec**

Run: `uv run pytest tests/test_pipeline.py -q`
Expected: FAIL (le test multi-segments échoue : `run_extraction` itère encore sur des caractères de la chaîne / `source_file` absent).

- [ ] **Step 3 : Implémenter**

Remplacer `nerve/pipeline.py` par :

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

async def run_extraction(cfg: Config, store: Store, doc_id: int,
                        segments: list[tuple[str, str]], *, client=None
                        ) -> AsyncGenerator[dict, None]:
    """segments = liste de (text, source_file). Un seul segment pour text/url ;
    N pour un zip. resolver/deduper partagés -> dedup et fusion d'entités
    traversent tous les fichiers (résolution intra-document)."""
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=None)

    async def embed_one(s: str) -> list[float]:
        return (await embed(cfg.embed, [s], client=client))[0]

    resolver = EntityResolver(store, doc_id, embed_one, cfg.entity_threshold)
    deduper = FactDeduper(embed_one, cfg.dedup_threshold, field=cfg.dedup_field)
    try:
        for text, source_file in segments:
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
                            subject_entity_id=sid, object_entity_id=oid,
                            source_file=source_file)
                        if not is_dup:
                            deduper.add(fid, vec)
                            store.add_fact_vector(fid, vec)
                        yield {"type": "fact", "fact": {**fact, "id": fid},
                               "is_duplicate": is_dup, "source_file": source_file}
                yield {"type": "round_end", "chunk": ci, "source_file": source_file}
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

Puis dans `nerve/api.py`, **mettre à jour l'appel existant** (handler `POST /api/documents`) :

```python
    async for _ in run_extraction(cfg, store, doc_id, [(body.text, "")]):
```

- [ ] **Step 4 : Vérifier le succès**

Run: `uv run pytest tests/test_pipeline.py tests/test_api.py -q`
Expected: PASS (test_pipeline : 2 ; test_api : 2 inchangés).

- [ ] **Step 5 : Commit**

```bash
git add nerve/pipeline.py nerve/api.py tests/test_pipeline.py
git commit -m "feat(i2): pipeline sur segments (text, source_file) — dedup cross-fichier"
```

---

## Task 9 : api.py — champ `url` sur `POST /api/documents` + correctif M-2

**Files:**
- Modify: `nerve/api.py`
- Test: `tests/test_api.py`

- [ ] **Step 1 : Écrire les tests rouges**

Ajouter à `tests/test_api.py` :

```python
async def fake_transcode(cfg, url, *, client=None):
    return ("# Page\n\ncorps de la page", "Page")

def test_create_document_from_url(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")
    monkeypatch.setattr(pipe, "stream_chat", fake_stream)
    monkeypatch.setattr(pipe, "embed", fake_embed)
    import importlib, nerve.api as api
    importlib.reload(api)
    monkeypatch.setattr(api, "transcode_url", fake_transcode)
    client = TestClient(api.app)
    r = client.post("/api/documents", json={"url": "https://ex.com/a"})
    assert r.status_code == 200
    doc_id = r.json()["document_id"]
    doc = client.get(f"/api/documents/{doc_id}/facts").json()["document"]
    assert doc["source_kind"] == "url"
    assert doc["source_ref"] == "https://ex.com/a"
    assert doc["title"] == "Page"                       # titre transcodé (body.title défaut)
    assert "dedup_field" in doc["params_json"]          # M-2 corrigé

def test_create_document_requires_text_or_url(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    import importlib, nerve.api as api
    importlib.reload(api)
    client = TestClient(api.app)
    assert client.post("/api/documents", json={}).status_code == 400
```

- [ ] **Step 2 : Vérifier l'échec**

Run: `uv run pytest tests/test_api.py -q`
Expected: FAIL (`url` non géré ; `source_kind == "text"` ; pas de validation 400).

- [ ] **Step 3 : Implémenter**

Dans `nerve/api.py` :

Ajouter l'import (après `from nerve.pipeline import run_extraction`) :

```python
from nerve.transcode import transcode_url
from fastapi import Form, UploadFile, File
```

Remplacer la classe `CreateDoc` :

```python
class CreateDoc(BaseModel):
    title: str = "Sans titre"
    text: str = ""
    url: str | None = None
    set_id: int | None = None
    set_name: str = "Défaut"
```

Remplacer le handler `create_document` :

```python
@app.post("/api/documents")
async def create_document(body: CreateDoc):
    set_id = body.set_id or store.create_set(body.set_name)
    if body.url:
        md, transcoded_title = await transcode_url(cfg, body.url)
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
    async for _ in run_extraction(cfg, store, doc_id, segments):
        pass  # Plan 1/I-2 : on consomme jusqu'au bout (SSE live = I-3)
    doc = store.get_document(doc_id)
    return {"document_id": doc_id, "total_facts": doc["total_facts"],
            "unique_facts": doc["unique_facts"],
            "duplicate_facts": doc["duplicate_facts"],
            "status": doc["status"]}
```

- [ ] **Step 4 : Vérifier le succès**

Run: `uv run pytest tests/test_api.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5 : Commit**

```bash
git add nerve/api.py tests/test_api.py
git commit -m "feat(i2): POST /api/documents accepte url (transcode) + persiste dedup_field (M-2)"
```

---

## Task 10 : api.py — `POST /api/documents/upload`

**Files:**
- Modify: `nerve/api.py`
- Test: `tests/test_api.py`

- [ ] **Step 1 : Écrire le test rouge**

Ajouter à `tests/test_api.py` (en tête : `import io`, `import zipfile`) :

```python
import io
import zipfile

def test_upload_zip(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")
    monkeypatch.setattr(pipe, "stream_chat", fake_stream)
    monkeypatch.setattr(pipe, "embed", fake_embed)
    import importlib, nerve.api as api
    importlib.reload(api)
    client = TestClient(api.app)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.txt", "le chat dort")
        z.writestr("bad.txt", "")                   # vide -> skipped
    r = client.post("/api/documents/upload",
                    files={"file": ("c.zip", buf.getvalue(), "application/zip")},
                    data={"set_name": "S"})
    assert r.status_code == 200
    body = r.json()
    assert body["skipped"] == ["bad.txt"]
    assert body["total_facts"] == 2                 # fake_stream émet 2 faits (1 doublon)
    doc = client.get(f"/api/documents/{body['document_id']}/facts").json()["document"]
    assert doc["source_kind"] == "file"
    assert doc["source_ref"] == "c.zip"

def test_upload_unreadable_file_fails_loud(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")
    import importlib, nerve.api as api
    importlib.reload(api)
    client = TestClient(api.app)
    r = client.post("/api/documents/upload",
                    files={"file": ("vide.txt", b"   ", "text/plain")},
                    data={"set_name": "S"})
    assert r.status_code == 422
```

- [ ] **Step 2 : Vérifier l'échec**

Run: `uv run pytest tests/test_api.py -q`
Expected: FAIL (`404`/`405` : endpoint `/api/documents/upload` absent).

- [ ] **Step 3 : Implémenter**

Dans `nerve/api.py`, ajouter l'import en tête (avec les autres `nerve.*`) :

```python
from nerve.ingest import ingest_upload, IngestError
```

Ajouter le handler (après `create_document`) :

```python
@app.post("/api/documents/upload")
async def upload_document(file: UploadFile = File(...), set_id: int | None = Form(None),
                          set_name: str = Form("Défaut"), title: str = Form("")):
    raw = await file.read()
    sid = set_id or store.create_set(set_name)
    doc_id = store.create_document(sid, title or file.filename, "file",
                                   source_ref=file.filename,
                                   params={"dedup_field": cfg.dedup_field})
    dest = os.path.join(cfg.data_dir, "inputs", str(doc_id))
    try:
        segments, skipped = ingest_upload(file.filename, raw, dest)
    except IngestError as e:
        store.finish_document(doc_id, error=str(e))
        raise HTTPException(status_code=422, detail=str(e))
    async for _ in run_extraction(cfg, store, doc_id, segments):
        pass
    doc = store.get_document(doc_id)
    return {"document_id": doc_id, "total_facts": doc["total_facts"],
            "unique_facts": doc["unique_facts"],
            "duplicate_facts": doc["duplicate_facts"],
            "status": doc["status"], "skipped": skipped}
```

- [ ] **Step 4 : Vérifier le succès**

Run: `uv run pytest tests/test_api.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5 : Vérifier la suite complète**

Run: `uv run pytest -q`
Expected: PASS (suite I-1 + I-2 ; ≈ 60 tests : 31 actuels + 29 ajoutés — transcode 12, ingest 10, config +2, pipeline +1, api +4).

- [ ] **Step 6 : Commit**

```bash
git add nerve/api.py tests/test_api.py
git commit -m "feat(i2): POST /api/documents/upload (fichier/zip multipart) + skipped"
```

---

## Vérifications réelles (smoke — hors tests automatiques, Ollama requis)

Après les 10 tâches, piloté par le contrôleur (cf. spec §11) :

1. Supprimer si besoin une vieille DB : `rm -f data/nerve.db*` (le schéma ne change pas, mais `data/inputs/` est nouveau).
2. `uv run nerve` (port 3000).
3. **URL** (trafilatura local) :
   `curl -s localhost:3000/api/documents -H 'Content-Type: application/json' -d '{"url":"https://fr.wikipedia.org/wiki/Cluny"}'` → faits extraits, `source_kind=url`.
4. **Zip** multi-fichiers :
   `curl -s -F file=@corpus.zip -F set_name=Test localhost:3000/api/documents/upload` → `skipped` cohérent, `source_file` rempli par fait (`GET /api/documents/{id}/facts`), une entité présente dans 2 fichiers est bien fusionnée.
5. **docx** réel via `/upload`.

---

## Self-Review (couverture spec → tâches)

- Spec §4 transcode (interface, chaîne, repli, fail-loud) → **T2, T3, T4**. Backends trafilatura/puremd/jina → T2/T3. Endpoint Pure.md (`https://pure.md/{url}` + `x-puremd-api-token`) et trafilatura (`output_format="markdown"`, `extract_metadata().title`) vérifiés via doc.
- Spec §5 ingest (read_file dispatch, IngestError, zip sûr anti-traversal, résilience de lot, mono-fichier fail-loud) → **T6, T7**.
- Spec §6 config (`url_transcoders`, `puremd_token`, `jina_key`) → **T1**.
- Spec §7 pipeline (segments, resolver/deduper partagés, `source_file`) → **T8**.
- Spec §8 API (`url` sur `/documents`, `/documents/upload`, M-2 `dedup_field` persisté, 422 fail-loud) → **T9, T10**.
- Spec §10 dépendance `python-docx` → **T5**.
- Spec §9 tests sans réseau (httpx `MockTransport`, monkeypatch trafilatura/pypdf, zip/docx générés) → tâches T1-T10.
- Hors périmètre (SSE/scheduler I-3 ; recherche I-4 ; zip→set ; fusion cross-doc) : non implémenté ✔ (l'upload consomme `run_extraction` synchroniquement).

Consistance des signatures vérifiée : `read_file(path, name)`, `ingest_upload(filename, raw, dest_dir) -> (segments, skipped)`, `transcode_url(cfg, url, *, client=None) -> (md, title)`, backends `(_name)(url, cfg, *, client)`, `run_extraction(cfg, store, doc_id, segments, *, client=None)`. Pas de placeholder.
