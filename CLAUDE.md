# nerve — instructions projet (pour Claude Code)

`nerve` (**N**amed **E**ntity **R**elation **V**isualizer & **E**xtractor) = outil **local,
mono-utilisateur** (Mac) d'extraction et de visualisation de graphes de connaissances.
Pipeline : texte / URL / fichiers → **extraction LLM streamée** → résolution & fusion d'entités
+ déduplication de faits → **SQLite (+ sqlite-vec)** → navigation **3 niveaux**
(Document / Set / Transverse) + recherche sémantique → page web **force-graph**.

## Commandes

- **Lancer** : `uv run nerve` → http://127.0.0.1:3000 (port configurable via `NERVE_PORT`).
- **Tests** : `uv run pytest -q` — suite **hermétique, sans réseau** (LLM/embeddings mockés).
- **Ajouter une dépendance** : `uv add <pkg>`. **Jamais** `pip` ni `python` en direct : tout passe par **`uv`**.
- **Réinitialiser la DB de dev** : `rm -f data/nerve.db*` puis relancer (cf. « DB jetable »).

## Architecture (modules `nerve/`)

- `config` — chargement env (providers LLM/embeddings **séparés**, seuils, transcodeurs).
- `textutil` — chunking.
- `extract` — prompt + parseur de **flux JSON** (faits streamés au fil de l'eau).
- `llm` — client httpx, **fail-loud**.
- `embeddings` — vecteurs (par défaut bge-m3, 1024 dims).
- `entities` — résolution + fusion de nœuds (**garde hybride** : cosinus ≥ seuil **ET** lien lexical).
- `dedup` — déduplication au niveau **fait** (cosinus).
- `transcode` — URL → markdown, backends **enfichables** (trafilatura/puremd/jina), fail-loud si tous échouent.
- `ingest` — fichiers pdf/docx/html/zip (zip **résilient** sur le lot ; mono-fichier illisible = fail-loud).
- `scheduler` — file FIFO **asyncio** mono-worker + **SSE** live + reprise par chunk.
- `pipeline` — orchestration de l'extraction.
- `graph` — `build_graph` **pur** → `{nodes, links}` (collapse cross-document par `normalized_key`).
- `store` — SQLite + sqlite-vec ; **toutes** les requêtes vivent ici.
- `api` — FastAPI (13 endpoints `/api/*` + routes statiques `/`, `/graph.js`, `/theme.css`).
- `web/` — `index.html` + `graph.js` (force-graph + graphology) + `theme.css` (palette « scriptorium »).

## Conventions impératives

- **TDD strict** : test d'abord (rouge), puis implémentation (vert). Un fichier de test par module (`tests/`).
- **Fail-loud** : les erreurs **remontent**, jamais avalées. Pas de `except: pass`, pas de fallback qui masque.
- **`uv` pour tout** : exécution, tests, dépendances.
- **Workflow = une branche par incrément** : spec → plan → impl, **PR vers `main`**, merge commit,
  revue `gemini-code-assist[bot]`. Exécution subagent-driven *lean* (1 sous-agent implémenteur par tâche TDD).
- **DB de dev jetable, sans migration** : le schéma évolue **par recréation**. À tout changement de schéma
  → `rm -f data/nerve.db*` (la DB est dans `.gitignore`). N'écris **aucune** migration.
- **Providers configurables** LLM ↔ embeddings, séparés, via env (endpoints OpenAI-compatibles).
  Défaut : Ollama `qwen3.6` + `bge-m3` (1024 dims) ; bascule OpenRouter possible par env.
- **Front anti-XSS** : texte issu du LLM via `textContent` / `replaceChildren` (**jamais** `innerHTML`) ;
  labels force-graph via `escapeHtml`.
- **graphology louvain en ESM** : import `graphology-communities-louvain/+esm` (module async → garder).
  Le bundle UMD `graphology-library` **casse** en navigateur (dépendance Node `crypto.getHashes`).
  La centralité vient du core `graphology` (`g.degree()`), **pas** de `graphology-metrics`.

## Pièges connus

- **Pyright = faux positifs** : il ne voit pas le venv `uv`. Le **seul** juge des types/imports est
  `uv run pytest`. Ne « corrige » rien sur la seule foi des diagnostics Pyright.
- **Handlers FastAPI qui mutent le scheduler = `async def`** (sinon threadpool → `asyncio.Queue` /
  connexion SQLite non thread-safe).
- **sqlite-vec applique le `WHERE` après le KNN** : pour filtrer par set, over-fetch puis tronquer en Python.
- La connexion SQLite est ouverte avec `check_same_thread=False` (endpoints sync hors thread d'ouverture).

## Langue

Tout le projet est en **français** : commits, specs, UI, docs. Réponses et livrables en français,
orthographe et accents corrects.

## Backlog

Dette technique + évolutions tracées en **issues GitHub #7–#13** (`Sandjab/nerve`).
