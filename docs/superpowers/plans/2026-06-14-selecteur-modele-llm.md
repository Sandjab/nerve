# Sélecteur de modèle LLM d'extraction — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permettre de choisir, depuis l'UI, le modèle LLM d'extraction parmi les modèles disponibles côté provider.

**Architecture:** Backend stateless — un endpoint `GET /api/models` liste les modèles (endpoint `/models` OpenAI-compatible) ; le modèle choisi voyage avec chaque `POST /api/documents`, est persisté dans les `params` du document, puis appliqué par le worker via `dataclasses.replace(cfg.llm, model=…)`. Front : dropdown dans `#top`, mémorisée en `localStorage`.

**Tech Stack:** FastAPI, httpx (async), Pydantic, SQLite, JS vanilla + force-graph. Tests : pytest (hermétiques, `httpx.MockTransport`, `TestClient` sans lifespan).

---

## Référence spec
`docs/superpowers/specs/2026-06-14-selecteur-modele-llm-design.md`. Décisions : LLM d'extraction uniquement (embeddings intouchés), application par extraction + `localStorage`, liste + tooltip.

## Structure des fichiers
- `nerve/llm.py` — **modifier** : ajouter `list_models()`. Responsabilité : client provider (chat + liste modèles).
- `nerve/api.py` — **modifier** : endpoint `GET /api/models` ; champ `model` dans `CreateDoc` + stockage en params.
- `nerve/scheduler.py` — **modifier** : appliquer l'override de modèle par document.
- `nerve/web/index.html`, `graph.js`, `theme.css` — **modifier** : dropdown.
- Tests : `tests/test_llm.py`, `tests/test_api.py`, `tests/test_scheduler.py`.

---

## Task 1 : `list_models()` dans `llm.py`

**Files:**
- Modify: `nerve/llm.py`
- Test: `tests/test_llm.py`

- [ ] **Step 1 : Écrire les tests qui échouent**

Ajouter à la fin de `tests/test_llm.py` (le module importe déjà `httpx`, `pytest`, `ProviderConfig`) :

```python
from nerve.llm import list_models

async def test_list_models_returns_ids():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"data": [{"id": "qwen3.6"}, {"id": "bge-m3"}]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = ProviderConfig("http://x/v1", "k", "m")
    assert await list_models(cfg, client=client) == ["qwen3.6", "bge-m3"]

async def test_list_models_raises_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = ProviderConfig("http://x/v1", "k", "m")
    with pytest.raises(httpx.HTTPStatusError):
        await list_models(cfg, client=client)
```

- [ ] **Step 2 : Lancer les tests pour vérifier l'échec**

Run: `uv run pytest tests/test_llm.py -q`
Expected: FAIL (`ImportError: cannot import name 'list_models'`).

- [ ] **Step 3 : Implémenter `list_models`**

Ajouter dans `nerve/llm.py` (après `stream_chat`) :

```python
async def list_models(
    cfg: ProviderConfig,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """Liste les ids de modèles d'un endpoint /models OpenAI-compatible
    (Ollama, OpenRouter, OpenAI...). Fail-loud : lève sur statut non-2xx."""
    headers = {"Authorization": f"Bearer {cfg.api_key}"}
    url = f"{cfg.base_url.rstrip('/')}/models"
    owns = client is None
    client = client or httpx.AsyncClient(timeout=30)
    try:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json().get("data") or []
        return [m["id"] for m in data if m.get("id")]
    finally:
        if owns:
            await client.aclose()
```

- [ ] **Step 4 : Lancer les tests pour vérifier le succès**

Run: `uv run pytest tests/test_llm.py -q`
Expected: PASS (tous).

- [ ] **Step 5 : Commit**

```bash
git add nerve/llm.py tests/test_llm.py
git commit -m "feat(llm): list_models() pour lister les modèles du provider"
```

---

## Task 2 : endpoint `GET /api/models`

**Files:**
- Modify: `nerve/api.py` (import + endpoint)
- Test: `tests/test_api.py`

- [ ] **Step 1 : Écrire les tests qui échouent**

Ajouter à la fin de `tests/test_api.py` :

```python
def test_models_endpoint_excludes_embed(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")
    import importlib, nerve.api as api
    importlib.reload(api)
    async def fake_list(cfg, *, client=None):
        return ["qwen3.6", api.cfg.embed.model, "gemma4"]
    monkeypatch.setattr(api, "list_models", fake_list)
    client = TestClient(api.app)
    res = client.get("/api/models").json()
    assert api.cfg.embed.model not in res["models"]      # embed exclu de la liste LLM
    assert res["default"] == api.cfg.llm.model
    assert "qwen3.6" in res["models"] and "gemma4" in res["models"]

def test_models_endpoint_provider_down_502(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")
    import importlib, nerve.api as api
    importlib.reload(api)
    async def boom(cfg, *, client=None):
        raise RuntimeError("provider down")
    monkeypatch.setattr(api, "list_models", boom)
    client = TestClient(api.app)
    assert client.get("/api/models").status_code == 502
```

- [ ] **Step 2 : Lancer les tests pour vérifier l'échec**

Run: `uv run pytest tests/test_api.py -q -k models`
Expected: FAIL (404 sur `/api/models` ou `AttributeError: module 'nerve.api' has no attribute 'list_models'`).

- [ ] **Step 3 : Implémenter l'endpoint**

Dans `nerve/api.py`, ajouter l'import (à côté des autres imports `nerve.*`) :

```python
from nerve.llm import list_models
```

Puis ajouter l'endpoint (par ex. juste après `create_document`) :

```python
@app.get("/api/models")
async def list_llm_models():
    try:
        models = await list_models(cfg.llm)
    except Exception as e:                       # fail-loud, message exploitable côté UI
        raise HTTPException(status_code=502, detail=f"Modèles indisponibles : {e}")
    return {"models": [m for m in models if m != cfg.embed.model],
            "default": cfg.llm.model}
```

- [ ] **Step 4 : Lancer les tests pour vérifier le succès**

Run: `uv run pytest tests/test_api.py -q -k models`
Expected: PASS.

- [ ] **Step 5 : Commit**

```bash
git add nerve/api.py tests/test_api.py
git commit -m "feat(api): GET /api/models (liste OpenAI-compatible, embed exclu, fail-loud)"
```

---

## Task 3 : champ `model` dans `CreateDoc` + stockage en params

**Files:**
- Modify: `nerve/api.py` (`CreateDoc`, `create_document`)
- Test: `tests/test_api.py`

- [ ] **Step 1 : Écrire les tests qui échouent**

Ajouter à `tests/test_api.py` :

```python
def test_create_document_stores_model_in_params(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")
    import importlib, nerve.api as api
    importlib.reload(api)
    client = TestClient(api.app)
    r = client.post("/api/documents", json={"text": "le chat dort", "model": "gemma4"})
    assert r.status_code == 200
    doc = client.get(f"/api/documents/{r.json()['document_id']}").json()
    assert json.loads(doc["params_json"])["model"] == "gemma4"

def test_create_document_without_model_omits_key(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")
    import importlib, nerve.api as api
    importlib.reload(api)
    client = TestClient(api.app)
    r = client.post("/api/documents", json={"text": "le chat dort"})
    doc = client.get(f"/api/documents/{r.json()['document_id']}").json()
    params = json.loads(doc["params_json"])
    assert "model" not in params and params["dedup_field"] == api.cfg.dedup_field
```

- [ ] **Step 2 : Lancer les tests pour vérifier l'échec**

Run: `uv run pytest tests/test_api.py -q -k "stores_model or without_model"`
Expected: FAIL (`model` absent de params).

- [ ] **Step 3 : Implémenter**

Dans `nerve/api.py`, ajouter le champ à `CreateDoc` :

```python
class CreateDoc(BaseModel):
    title: str = "Sans titre"
    text: str = ""
    url: str | None = None
    set_id: int | None = None
    set_name: str = "Défaut"
    model: str | None = None
```

Dans `create_document`, construire `params` une fois et l'utiliser dans les deux branches. Remplacer le corps actuel (depuis `set_id = …` jusqu'au `else:` inclus) par :

```python
    set_id = body.set_id or store.create_set(body.set_name)
    params = {"dedup_field": cfg.dedup_field}
    if body.model:
        params["model"] = body.model
    if body.url:
        try:
            md, transcoded_title = await transcode_url(cfg, body.url)
        except RuntimeError as e:
            raise HTTPException(status_code=422, detail=str(e))
        title = body.title if body.title != "Sans titre" else (transcoded_title or body.url)
        doc_id = store.create_document(set_id, title, "url", source_ref=body.url,
                                       params=params)
        segments = [(md, "")]
    elif body.text:
        doc_id = store.create_document(set_id, body.title, "text", params=params)
        segments = [(body.text, "")]
    else:
        raise HTTPException(status_code=400, detail="text ou url requis")
```

(Le reste de la fonction — `write_segments`, `scheduler.enqueue`, `return` — est inchangé.)

- [ ] **Step 4 : Lancer les tests pour vérifier le succès**

Run: `uv run pytest tests/test_api.py -q`
Expected: PASS (y compris l'existant `test_create_document_from_url_enqueues` qui vérifie `dedup_field`).

- [ ] **Step 5 : Commit**

```bash
git add nerve/api.py tests/test_api.py
git commit -m "feat(api): CreateDoc.model + stockage dans params du document"
```

---

## Task 4 : appliquer l'override de modèle dans le worker

**Files:**
- Modify: `nerve/scheduler.py` (import `dataclasses`, helper `_cfg_for`, `_process`)
- Test: `tests/test_scheduler.py`

- [ ] **Step 1 : Écrire les tests qui échouent**

Ajouter à `tests/test_scheduler.py` (le module importe déjà `asyncio`, `write_segments`, `Scheduler`, `Store`, `load_config`) :

```python
async def _run_capturing(captured):
    async def fake_run(cfg, store, doc_id, segments, *, start_segment=0, start_chunk=0, client=None):
        captured["model"] = cfg.llm.model
        store.finish_document(doc_id)
        d = store.get_document(doc_id)
        yield {"type": "done", "total_facts": d["total_facts"],
               "unique_facts": d["unique_facts"], "duplicate_facts": d["duplicate_facts"]}
    return fake_run

async def _drain_until_done(sched, sub):
    try:
        while True:
            ev = await asyncio.wait_for(sub.get(), timeout=2)
            if ev.get("type") == "done":
                break
    finally:
        await sched.stop()

async def test_worker_applies_model_override(tmp_path):
    captured = {}
    st = Store(str(tmp_path / "ov.db"), embed_dim=2); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text", params={"model": "gemma4"})
    write_segments(str(tmp_path), doc_id, [("texte", "")])
    sched = Scheduler(load_config(), st, run=await _run_capturing(captured), data_dir=str(tmp_path))
    sub = sched.subscribe(doc_id)
    sched.start(); sched.enqueue(doc_id)
    await _drain_until_done(sched, sub)
    assert captured["model"] == "gemma4"

async def test_worker_uses_default_model_without_override(tmp_path):
    captured = {}
    st = Store(str(tmp_path / "def.db"), embed_dim=2); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text")     # pas de params model
    write_segments(str(tmp_path), doc_id, [("texte", "")])
    cfg = load_config()
    sched = Scheduler(cfg, st, run=await _run_capturing(captured), data_dir=str(tmp_path))
    sub = sched.subscribe(doc_id)
    sched.start(); sched.enqueue(doc_id)
    await _drain_until_done(sched, sub)
    assert captured["model"] == cfg.llm.model
```

- [ ] **Step 2 : Lancer les tests pour vérifier l'échec**

Run: `uv run pytest tests/test_scheduler.py -q -k "override or default_model"`
Expected: FAIL au test override (le worker passe `self.cfg` tel quel → `captured["model"]` vaut le défaut `qwen3.6`, pas `gemma4`).

- [ ] **Step 3 : Implémenter**

Dans `nerve/scheduler.py`, ajouter en tête (avec les autres imports) :

```python
import dataclasses
```

Ajouter une méthode helper dans la classe `Scheduler` (par ex. juste avant `_process`) :

```python
    def _cfg_for(self, doc: dict):
        """Config effective : modèle d'extraction surchargé si présent dans les
        params du document, sinon cfg par défaut. Override par-document (stateless)."""
        params = json.loads(doc.get("params_json") or "{}")
        model = params.get("model")
        if not model:
            return self.cfg
        return dataclasses.replace(self.cfg, llm=dataclasses.replace(self.cfg.llm, model=model))
```

Dans `_process`, remplacer la ligne :

```python
        gen = self._run(self.cfg, self.store, doc_id, segments,
                        start_segment=ps, start_chunk=pc)
```

par :

```python
        gen = self._run(self._cfg_for(doc), self.store, doc_id, segments,
                        start_segment=ps, start_chunk=pc)
```

- [ ] **Step 4 : Lancer les tests pour vérifier le succès**

Run: `uv run pytest tests/test_scheduler.py -q`
Expected: PASS (existants + nouveaux).

- [ ] **Step 5 : Commit**

```bash
git add nerve/scheduler.py tests/test_scheduler.py
git commit -m "feat(scheduler): modèle d'extraction par document (override cfg.llm)"
```

---

## Task 5 : dropdown front

**Files:**
- Modify: `nerve/web/index.html` (select dans `#top`)
- Modify: `nerve/web/graph.js` (peuplement, localStorage, envoi dans le POST)
- Modify: `nerve/web/theme.css` (style du select)

> Pas de test unitaire (le front n'est pas couvert par pytest) → vérification manuelle en Step 4.

- [ ] **Step 1 : `index.html` — ajouter le select dans `#top`**

Remplacer le bloc `#top` :

```html
  <div id="top">
    <textarea id="txt" placeholder="Colle un texte à transformer en graphe…"></textarea>
    <button id="go">Extraire</button>
  </div>
```

par :

```html
  <div id="top">
    <textarea id="txt" placeholder="Colle un texte à transformer en graphe…"></textarea>
    <select id="llmModel" title="Modèle d'extraction — rapide : qwen2.5:7b-instruct · qualité : qwen3.6"></select>
    <button id="go">Extraire</button>
  </div>
```

- [ ] **Step 2 : `graph.js` — peuplement + mémorisation + envoi**

(a) Ajouter `loadModels()` et son écouteur (par ex. près de `loadSets`) :

```javascript
async function loadModels(){
  const sel = document.getElementById("llmModel");
  const saved = localStorage.getItem("nerve-llm-model");
  try {
    const {models, default: def} = await getJSON("/api/models");
    sel.replaceChildren(...models.map(m => {
      const o = document.createElement("option"); o.value = m; o.textContent = m; return o;
    }));
    sel.value = (saved && models.includes(saved)) ? saved : def;
  } catch(err) {
    showError("Modèles indisponibles : " + err.message);   // fail-loud, sans masquer
    if(saved){                                              // repli : dernier choix connu
      const o = document.createElement("option"); o.value = saved; o.textContent = saved;
      sel.replaceChildren(o); sel.value = saved;
    }
    // sinon select vide -> POST sans model -> le backend retombe sur LLM_MODEL
  }
}
document.getElementById("llmModel").addEventListener("change", (e) => {
  localStorage.setItem("nerve-llm-model", e.target.value);
});
```

(b) Dans le handler `#go`, ajouter le modèle au corps du POST. Remplacer :

```javascript
      body:JSON.stringify({title:"Coller", text})}));
```

par :

```javascript
      body:JSON.stringify({title:"Coller", text,
        model: document.getElementById("llmModel").value || undefined})}));
```

(c) Appeler `loadModels()` à l'initialisation. Remplacer la dernière ligne `loadSets();` par :

```javascript
loadSets();
loadModels();
```

- [ ] **Step 3 : `theme.css` — styliser le select dans `#top`**

Ajouter, juste après la règle `#top button { … }` :

```css
#top #llmModel{height:46px;border-radius:6px;border:1px solid var(--line);padding:0 8px;
  background:var(--card);color:var(--ink);font-family:inherit;max-width:190px;cursor:pointer}
```

- [ ] **Step 4 : Vérification manuelle**

```bash
uv run pytest -q          # toute la suite verte (aucune régression)
uv run nerve              # http://127.0.0.1:3000
```

Vérifier dans le navigateur (Ollama doit tourner pour `/api/models`) :
1. La dropdown `#llmModel` se peuple (qwen3.6, qwen2.5:7b-instruct, gemma4, gpt-oss… ; **bge-m3 absent**), défaut présélectionné.
2. Choisir un modèle → recharger la page → le choix est conservé (localStorage).
3. Coller un texte, « Extraire » → l'extraction aboutit ; vérifier dans `ollama ps` (ou le log) que le modèle chargé est bien celui choisi.
4. Tooltip présent au survol du select.

- [ ] **Step 5 : Commit**

```bash
git add nerve/web/index.html nerve/web/graph.js nerve/web/theme.css
git commit -m "feat(web): dropdown de sélection du modèle d'extraction"
```

---

## Self-review (réalisée)

**1. Couverture spec :**
- `GET /api/models` (OpenAI-compatible, embed exclu, fail-loud) → Task 1 + 2 ✓
- Override `model` par `POST /api/documents`, stateless, repli défaut → Task 3 (stockage) + Task 4 (application) ✓
- Dropdown `#top`, peuplée au chargement, `localStorage`, tooltip → Task 5 ✓
- Tests hermétiques TDD → chaque task ✓
- Embeddings hors périmètre → aucune task n'y touche ✓

**2. Placeholders :** aucun — chaque step donne le code complet et les commandes exactes.

**3. Cohérence des types/noms :** `list_models(cfg, *, client=None)` (llm) ↔ importé et appelé `await list_models(cfg.llm)` (api) ↔ mocké `fake_list(cfg, *, client=None)` (test). `params["model"]` (api) ↔ `params.get("model")` (scheduler `_cfg_for`). `nerve-llm-model` (localStorage) et `#llmModel` (id) cohérents index.html/graph.js/theme.css. `dataclasses.replace` sur `Config` et `ProviderConfig` (tous deux frozen dataclasses) ✓.

## Workflow
Branche `feat-selecteur-modele-llm` → 5 commits → PR vers `main`, revue `gemini-code-assist[bot]`.
