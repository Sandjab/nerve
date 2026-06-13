# nerve — conception du portage (et refonte) de knowledge-graph-extractor

**Date** : 2026-06-13
**Statut** : design validé en brainstorming, en attente de relecture avant plan d'implémentation
**Repo** : `nerve` (vide au départ : LICENSE + README)

## 1. Vision

`nerve` est un **outil générique et indépendant** d'extraction et de visualisation de
**graphes de connaissances** à partir de documents hétérogènes : aussi bien une monographie
de *scriptorium*, un article de *Curax*, qu'un roman comme *Les Heures creuses*. Chaque fait
extrait est une arête `(sujet) —[prédicat]→ (objet)`, enrichie d'un titre, d'une description,
d'un span de preuve, d'une confiance, de tags et de la source.

Il dérive de `../knowledge-graph-extractor` (FastAPI + llama.cpp/CUDA, mono-fichier de 89 Ko),
mais c'est une **refonte pour un usage mono-utilisateur local sur Mac (Apple Silicon)**, pas un
portage ligne à ligne. On réutilise l'esprit du pipeline et les prompts ; on remplace
l'infrastructure (LLM, embeddings, persistance, scheduler) et on restructure le code.

### Trois niveaux de navigation

| Niveau | Définition | Taille typique | Rendu |
|--------|-----------|----------------|-------|
| **Document** | une source ingérée (texte, URL, ou fichier/zip) → son graphe de faits | centaines de nœuds | force-graph |
| **Source Set** | un corpus nommé regroupant des documents (ex. « Scriptorium », « Curax », « Les Heures creuses ») | milliers | force-graph |
| **Subset transverse** | une requête (filtres + voisinage sémantique) **à travers plusieurs sets** ; **filtré/borné**, pas « tout le corpus d'un bloc » | centaines/milliers | force-graph |

> Un **zip** = **un document** dont les faits portent leur provenance (`source_file`), conformément
> à l'original. Éclater un zip en *set de documents* (un par fichier) est une option différée.

Le subset transverse étant borné, `force-graph` suffit aux trois niveaux. Le rendu « corpus
entier d'un coup » (10k–1M nœuds, GPU) est **hors périmètre v1** (voir §9).

## 2. Stack technique (décision : voie « web Python »)

Reste **Python + FastAPI**, lancé en **natif via `uv`** (Python 3.11+), **sans Docker** (aucun GPU
à isoler en local). Justification du choix de langage en §10.

- **LLM** : client **OpenAI-compatible générique** — configurable par `base_url` + `api_key` +
  `model`. Marche avec **n'importe quel endpoint OpenAI-like** : **Ollama** local
  (`http://localhost:11434/v1`, **défaut**, modèle `qwen3.6` déjà présent — prompts calibrés),
  **OpenRouter** (`https://openrouter.ai/api/v1`), OpenAI, etc. Implémenté en `httpx` (pas de SDK).
  Génération **streamée** + **sortie structurée** (support variable selon provider — voir §8).
- **Embeddings** : client **OpenAI-compatible générique** lui aussi (`/v1/embeddings`,
  `base_url`+`api_key`+`model`+`dim`), **indépendant** du provider LLM. Défaut : **Ollama
  `bge-m3`** (1024 dim) — choisi **multilingue** (corpus francophone : scriptorium, Curax, roman),
  là où `nomic-embed-text` est anglo-centré. Supprime le stack `torch`/`sentence-transformers`
  (~2 Go) ; `ollama pull bge-m3`. *Note : tous les providers OpenAI-like n'offrent pas d'embeddings
  (OpenRouter p.ex.) — d'où la config séparée ; on peut faire LLM distant + embeddings locaux.*
- **Ingestion** : texte collé · **URL** via un **transcodeur enfichable** (voir §2.1) · **zip**
  (pdf via `pypdf`, docx, html, md, code…). Chunking conservé (pas de troncature).
- **Dedup** : **`sqlite-vec`** (cosinus). Le dedup **intra-document** (fusionner les doublons à
  travers rounds/fichiers d'une même extraction) reste le cœur. Seuil à **re-régler** pour bge-m3
  (l'ancien 0.90 était calé sur jina).
- **Persistance** : **hybride** — **SQLite** (`nerve.db` : sets, documents, faits, index
  vectoriel) + **filesystem** (inputs bruts sous `data/inputs/<doc_id>/`).
- **Scheduler** : **file FIFO mono-worker** + **pause/annulation** + **reprise après crash**
  (état de dedup reconstruit depuis la DB). On **abandonne** la préemption/backfill multi-slot de
  l'original (elle servait à partager un GPU L4 entre utilisateurs).
- **Visualisation** : frontend web extrait du monolithe. **force-graph.js** pour le **rendu**
  (streaming des faits, cartes de fait au survol, **étiquettes d'arêtes optionnelles**, contrôle
  canvas total pour la palette). **graphology** comme couche **données/analytics** indépendante du
  moteur (communautés *louvain*, centralité, chemins). **Sigma** gardé comme **voie de bascule**
  documentée si le scale l'exige un jour (même couche graphology). Thèmes **clair + sombre**.

### 2.1 Transcodeur URL→markdown (enfichable, configurable)

Mécanisme **générique** : une **chaîne ordonnée de backends** partageant une interface commune
`transcode(url) -> (markdown, title)`. Configurable (ordre, activation, **repli** si un backend
échoue ou renvoie du vide) :

| Backend | Type | Clé | Note |
|---------|------|-----|------|
| `trafilatura` | local | — | **défaut** ; rien ne sort de la machine ; faible sur SPA 100 % JS |
| `puremd` | distant | `PUREMD_API_TOKEN` | Pure.md — markdown propre, sortie très peu verbeuse ; gère HTML/PDF/images |
| `jina` | distant | `JINA_API_KEY` | Jina Reader (`r.jina.ai`) |

Chaîne par défaut : `["trafilatura"]` (100 % local). Ajouter une clé active le backend distant
correspondant et l'insère dans la chaîne (ex. `URL_TRANSCODERS=trafilatura,puremd` : local d'abord,
Pure.md en repli sur les pages difficiles). **Ajouter un backend = un adaptateur** implémentant
l'interface. Les backends distants envoient l'URL/le contenu à un tiers (note de confidentialité).

## 3. Palette (scriptorium)

Reprise de `scriptorium/.claude/skills/monograph/template/charte.css`.

```
--paper:#F4F6FA  --card:#FFFFFF  --ink:#15202E  --ink-soft:#43536A  --ink-faint:#7A889B
--blue:#23537F   --blue-deep:#142E49  --blue-bright:#2C77B6  --blue-wash:#E7EEF6
--bordeaux:#7C2A38  --bordeaux-bright:#9B3443  --line:#D7DFE9
(+ 3e teinte source : vert #1C6A4C)
```

Coloration **sémantique** (togglable) : par **source set** · par **type** (entité vs valeur) ·
par **communauté** (louvain) · confiance encodée en opacité/épaisseur d'arête. Les **arêtes
inter-sources** (passerelles) sont mises en évidence (bordeaux) — ce sont elles qui matérialisent
le subset transverse. Thème sombre fourni par la charte également.

## 4. Modèle de données (SQLite + sqlite-vec)

```sql
source_sets(
  id, name, description, created_at
)

documents(                       -- une extraction = un document
  id, set_id REFERENCES source_sets(id),
  title, source_kind,            -- 'text' | 'url' | 'file'
  source_ref,                    -- url, nom de fichier, ou null
  status,                        -- queued | running | paused | done | failed
  params_json,                   -- k_rounds, dedup_field, dedup_threshold, model, prompt
  total_facts, unique_facts, duplicate_facts,
  created_at, finished_at, error
)

facts(
  id, document_id REFERENCES documents(id),
  subject, predicate, object,
  title, description, evidence_span,
  confidence,                    -- 0..100
  tags_json, source_file,        -- source_file : pour les jobs zip multi-fichiers
  is_duplicate, dup_of_id,
  created_at
)

-- index vectoriel sqlite-vec (1 ligne par fait non-dup)
vec_facts USING vec0(fact_id INTEGER PRIMARY KEY, embedding FLOAT[1024])
```

- **Inputs bruts** (zip, pdf, docx…) hors DB, sous `data/inputs/<doc_id>/`.
- **Normalisation des entités** : les nœuds sont des noms d'entités **normalisés** (les variantes
  fusionnent) — logique à porter depuis l'original (point qualité clé). Une vue/index des entités
  normalisées sert au **transverse** (repérer une entité présente dans plusieurs documents/sets).
- **Subset transverse** = requête : filtres (sets, tags, confiance) + voisinage `sqlite-vec` autour
  d'une entité/requête → assemble un sous-graphe borné `{nodes, links}`.
- **Recherche globale** = `sqlite-vec` sur `vec_facts`, tous documents confondus. **Pas de fusion
  cross-document automatique** : chaque graphe reste le sien ; le transverse est une *vue*.

## 5. Architecture / modules

```
nerve/
  pyproject.toml            # uv : deps + script console `nerve`
  nerve/
    config.py               # providers LLM/embeddings (base_url/api_key/model/dim), chaîne de transcodeurs, seuils, chemins
    llm.py                  # client OpenAI-compatible générique : génération streamée + sortie structurée
    embeddings.py           # client OpenAI-compatible générique (embeddings)
    transcode.py            # transcodeurs URL→md enfichables (trafilatura, puremd, jina) + chaîne de repli
    ingest.py               # texte / zip / lecture fichiers + chunking (délègue les URL à transcode.py)
    extract.py              # prompt + FACT_SCHEMA + parse incrémental des triplets
    dedup.py                # dedup sqlite-vec (cosinus, seuil)
    store.py                # couche SQLite (sets/documents/facts/vec) + inputs fichiers
    scheduler.py            # file FIFO mono-worker + pause/reprise (remplace jobs.py)
    graph.py                # assemblage faits → {nodes, links} pour le front
    api.py                  # routes FastAPI + flux SSE
    web/                    # frontend (extrait du monolithe)
      index.html
      app.js                # UI, SSE, navigation 3 niveaux
      graph.js              # force-graph (rendu) + graphology (analytics) + étiquettes optionnelles
      theme.css             # palette scriptorium, clair + sombre
  data/                     # gitignored
    nerve.db
    inputs/<doc_id>/
```

Chaque module reste petit et testable isolément. Le frontend cesse d'être une chaîne de ~900
lignes dans `app.py`.

## 6. Pipeline & scheduler

`ingest → chunk → extract (Ollama, streamé) → dedup (sqlite-vec) → store (SQLite) → SSE vers le
graphe`. Les faits arrivent **en direct** dans la visu (comme l'original).

Scheduler : **file FIFO, un worker**. On garde **pause/annulation** d'un document en cours et
**reprise après crash** (l'état dedup se reconstruit depuis les faits déjà en DB). On retire
préemption, backfill auto, distinction « user-held vs system-paused ».

## 7. API (FastAPI) + SSE

```
# Sets
POST   /api/sets                      créer un source set
GET    /api/sets                      lister
GET    /api/sets/{id}                 détail + documents

# Documents (extractions)
POST   /api/documents                 créer (text|url) → assigné à un set
POST   /api/documents/upload          créer depuis fichier/zip (multipart)
GET    /api/documents/{id}            métadonnées
GET    /api/documents/{id}/facts      faits + méta
GET    /api/documents/{id}/events     flux SSE (replay, fact, metrics, status, done, error)
POST   /api/documents/{id}/pause      pause utilisateur
POST   /api/documents/{id}/resume     reprise
DELETE /api/documents/{id}            suppression

# Transverse & recherche
GET    /api/search?q=&sets=&k=        recherche sémantique globale (sqlite-vec)
GET    /api/transverse?entity=&sets=&min_conf=   sous-graphe transverse borné {nodes, links}

# UI
GET    /                              page web
GET    /assets/{name}                 statiques
```

SSE conservé pour le streaming (mêmes types d'événements que l'original, simplifiés).

## 8. Risques & points à vérifier en implémentation (ne pas présumer)

- **Sortie structurée variable selon provider** : le support de `response_format:
  {type:"json_schema",…}` diffère (Ollama OK ; OpenRouter = passe-plat dépendant du modèle sous-
  jacent ; certains endpoints ne le gèrent pas). Le client doit **dégrader gracieusement** —
  le **parseur JSON incrémental** de l'original sert de filet. Confirmer aussi, côté Ollama, que
  streaming + sortie structurée se composent (doc via context7).
- **Endpoint exact de Pure.md à confirmer** : motif présumé « préfixe » (`https://pure.md/<url>`)
  + en-tête token (`x-puremd-api-token` ?). Vérifier la doc avant de câbler l'adaptateur `puremd`.
- **Embeddings absents chez certains providers** (OpenRouter) : géré par la **config séparée**
  LLM/embeddings (§2). Penser à valider que le provider d'embeddings choisi expose `/v1/embeddings`.
- **Paramètres d'échantillonnage** : `min_p`, `top_k`, `presence_penalty`, `chat_template_kwargs
  (enable_thinking=false)` sont propres à llama.cpp → mapper vers les `options` Ollama / le
  paramètre `think` (qwen3.6 peut être un modèle « thinking » → désactiver). Certains seront
  ignorés.
- **`sqlite-vec` + extensions sur macOS** : charger une extension nécessite
  `sqlite3.enable_load_extension`, **parfois désactivé** dans le `sqlite3` du Python système.
  Mitigation : Python géré par `uv` + paquet PyPI `sqlite-vec`, ou `pysqlite3-binary`/`apsw` si
  besoin. **À valider tôt** (risque de setup réel).
- **Dimension d'embedding figée** : la table `vec_facts` fixe la dim (bge-m3 = 1024). Changer de
  modèle d'embedding ⇒ réindexer.
- **Normalisation des entités** : qualité du graphe = qualité de la fusion des variantes de noms.
  Porter la logique de l'original ; la tester.
- **Re-réglage du seuil de dedup** pour bge-m3 (l'ancien 0.90 ne s'applique pas tel quel).

## 9. Hors périmètre v1 (YAGNI)

- Fusion/dedup **automatique cross-document** (chaque graphe reste le sien).
- Rendu **« corpus entier d'un bloc »** (10k–1M nœuds, GPU web type Cosmos.gl/Sigma) — la couche
  graphology étant indépendante du moteur, la bascule reste possible sans tout réécrire.
- Application **native** (Swift/Grape, Tauri/.app) — voir §10.
- Préemption multi-slot du scheduler, multi-utilisateur, auth.

## 10. Alternatives écartées (et pourquoi)

- **Réécriture native macOS (Swift + Grape)** : la lib `SwiftGraphs/Grape` rend la visu native
  *viable* (force-directed natif, pas seulement WKWebView). Écartée en v1 car : (a) la charge est
  **I/O-bound** (le temps mur est dominé par Ollama — la perf native ne départage rien) ; (b) elle
  imposerait de **réécrire tout le backend** (ingest/LLM/dedup/SQLite/scheduler), Grape ne couvrant
  que la visu ; (c) plafond ~quelques milliers de nœuds (Metal pas livré) ; (d) Grape pré-1.0,
  mainteneur unique. **La voie web Python réutilise ~90 % de l'existant et itère le plus vite.**
- **`.app` cliquable (Tauri/py2app + WKWebView)** : possible plus tard *au-dessus* du backend
  Python sans réécriture — laissé en option future, pas en v1.
- **Go / Rust / C** : Go serait l'alternatif crédible (binaire unique, Ollama est en Go) mais sans
  bénéfice réel sur cette charge ; Rust/C = sur-ingénierie (perf gâchée sur de l'I/O, C non sûr).
- **Embeddings via sentence-transformers (jina-v5-nano)** : fidèle au pipeline benchmarké mais
  ~2 Go de deps torch. Écarté au profit d'un embedder Ollama (un seul runtime).
- **Visualisation Sigma/Cosmos d'emblée** : repoussée — `force-graph` couvre les 3 niveaux bornés
  avec le meilleur *feel* streaming et le contrôle esthétique le plus fin ; graphology indépendant
  permet de basculer le rendu plus tard.

## 11. Dépendances

**Python** (via `uv`) : `fastapi`, `uvicorn`, `httpx`, `numpy`, `python-multipart`, `pypdf`,
`trafilatura`, `sqlite-vec` (+ `sqlite3`, `zipfile` stdlib). `httpx` couvre **tous** les providers
OpenAI-compatibles (LLM/embeddings) et les transcodeurs distants (puremd/jina) — **pas de SDK
`openai`, plus de `torch`.**

**Frontend** (CDN, déjà vérifiés) : `force-graph@1.43.5`, `graphology@0.25.4`,
`graphology-library@0.7.0` (louvain, métriques) ; `sigma@3.0.0` tenu en réserve.

**Runtimes externes** : Ollama (déjà installé) avec `qwen3.6` (présent) + `bge-m3` (à puller).

## 12. Setup ciblé (Mac M3 Ultra, 96 Go)

1. `ollama pull bge-m3` (qwen3.6 déjà présent).
2. `uv` crée l'environnement (Python 3.11+) depuis `pyproject.toml`.
3. `uv run nerve` → serveur local (port à fixer, ex. 3000) → ouvrir le navigateur.
4. Valider tôt le chargement de `sqlite-vec` (cf. §8).

### Configuration (exemple — env ou fichier)

```
LLM_BASE_URL=http://localhost:11434/v1    LLM_API_KEY=ollama    LLM_MODEL=qwen3.6
EMBED_BASE_URL=http://localhost:11434/v1  EMBED_API_KEY=ollama  EMBED_MODEL=bge-m3  EMBED_DIM=1024
URL_TRANSCODERS=trafilatura               # ajouter puremd / jina si les clés sont définies
# PUREMD_API_TOKEN=...    JINA_API_KEY=...
# Bascule OpenRouter (LLM) : LLM_BASE_URL=https://openrouter.ai/api/v1  LLM_API_KEY=sk-or-...  LLM_MODEL=...
```

Changer `EMBED_MODEL`/`EMBED_DIM` ⇒ **réindexer** `vec_facts` (dim figée). Providers LLM et
embeddings **indépendants** : possible de mettre le LLM sur OpenRouter et les embeddings en local.
