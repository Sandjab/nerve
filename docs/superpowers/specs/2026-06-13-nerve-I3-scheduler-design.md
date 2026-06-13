# nerve — Plan 2 / I-3 · Scheduler + SSE live + concurrence

**Date** : 2026-06-13
**Statut** : design validé en brainstorming, en attente de relecture avant plan d'implémentation
**Incrément** : I-3 (dépend du Plan 1 ; orthogonal à I-1/I-2 déjà mergés). Branche : `nerve-i3-scheduler`.
**Sources de cadrage** : spec globale §6 (pipeline & scheduler), §7 (API + SSE) ; HANDOFF Plan 2 §3.

## 1. But

Passer du modèle **synchrone bloquant** actuel (les endpoints exécutent `run_extraction`
jusqu'au bout, le front fait `fetch /facts` après) à une **couche d'exécution asynchrone** :
file FIFO mono-worker, **faits diffusés en direct** dans la visu (SSE), **pause/reprise**,
**reprise après crash**, et solde de la **dette concurrence** SQLite reportée du Plan 1.

## 2. Périmètre

**Dans I-3** :
- `scheduler.py` (nouveau) — file FIFO mono-worker + pub/sub + pause/reprise + reconcile au startup.
- `api.py` — `lifespan` (démarre worker + reconcile), enqueue (plus de consommation synchrone),
  endpoints `pause`/`resume`/`events` (SSE).
- `store.py` — transitions de statut, progression `(segment, chunk)`, méthodes de rechargement
  pour la reprise, **WAL + busy_timeout**.
- `pipeline.py` — point de reprise + état (resolver/deduper) pré-chargeable ; `round_end` enrichi.
- Persistance des **segments source** sur disque (`segments.jsonl`) pour rendre la reprise possible.
- `web/index.html` — consommation SSE minimale (faits en direct).

**Hors I-3** :
- `DELETE /api/documents/{id}` (cycle de vie complet) — différé.
- Métriques tokens/tps dans le SSE — non calculées (YAGNI).
- Rendu/visualisation soignés → I-5.
- **Abandonné de l'original** (`jobs.py`) : préemption multi-slot, backfill automatique,
  distinction foreground/`held`/`paused`-système. On garde une **file FIFO simple**.

## 3. Décisions arrêtées (brainstorming)

- **Modèle d'exécution** : `asyncio` in-process — `asyncio.Queue` de `doc_id` + une tâche worker
  unique lancée dans la `lifespan` FastAPI + bus pub/sub `asyncio.Queue` par doc. (Threads/process
  séparés = sur-ingénierie pour un outil local mono-worker ; le pipeline est déjà async via httpx.)
- **Granularité de reprise = par chunk** : on persiste `(segment, chunk)` ; à la reprise on saute
  jusqu'à ce point et on re-traite le minimum. `chunk_text` est déterministe → indices stables.
- **Concurrence = WAL + busy_timeout** seulement (pas de `to_thread` : YAGNI en mono-utilisateur,
  pipeline I/O-bound async, écritures SQLite locales et brèves ; `threadsafety=3` déjà en place).
- **Persistance des segments source** : nécessaire à la reprise (le texte collé et le markdown
  transcodé ne sont aujourd'hui jamais persistés). Format unique `segments.jsonl` sous
  `data/inputs/<doc_id>/` pour text/url/file confondus.
- **reconcile au startup** : un crash laisse des docs `running`/`queued` → **ré-enqueue** (reprise
  auto au point persisté) ; les docs `paused` (pause utilisateur explicite) **restent** `paused`.

## 4. `scheduler.py`

Classe `Scheduler` détenant l'état d'exécution (pas d'état global de module) :
- `queue: asyncio.Queue[int]` (doc_ids) ; `_subscribers: dict[int, list[asyncio.Queue]]` ;
  `_pause: set[int]` ; la tâche `_worker`.
- `enqueue(doc_id)` → statut `queued`, `queue.put_nowait(doc_id)`.
- **`_worker()` (boucle)** : `doc_id = await queue.get()` →
  1. charge les segments depuis `segments.jsonl` ;
  2. lit `(progress_segment, progress_chunk)` ; si > `(0,0)` → `rebuild_state` (resolver/deduper) ;
  3. statut `running`, `emit(status)` ;
  4. `async for ev in run_extraction(cfg, store, doc_id, segments, start_segment=…,
     start_chunk=…, resolver=…, deduper=…)` : `emit(doc_id, ev)` ; sur `round_end{segment,chunk}`
     → `store.set_progress(doc_id, segment, chunk+1)` ; si `doc_id in _pause` → statut `paused`,
     fermer le générateur (`aclose`), `emit(status paused)`, **stop** (ne pas marquer `done`) ;
  5. fin normale → `done` (déjà via le pipeline) ; exception → `error` (déjà via le pipeline).
- `subscribe(doc_id) -> asyncio.Queue` / `unsubscribe(doc_id, q)` / `emit(doc_id, event)`
  (`put_nowait`, on ignore les files pleines).
- `pause(doc_id)` : `_pause.add` ; si le doc n'est pas en cours → statut `paused` immédiat +
  retire de la file. `resume(doc_id)` : `_pause.discard` ; ré-`enqueue`.
- `reconcile()` : au startup, `store.list_resumable()` (`running`/`queued`) → `enqueue` chacun.

La pause est **coopérative aux frontières de chunk** (le worker la vérifie sur `round_end`). Le
pipeline reste « pur » : il n'écrit ni la progression ni ne gère la pause — c'est le worker.

## 5. Persistance des segments & reprise

- À l'enqueue, après transcode/ingest : écrire `data/inputs/<doc_id>/segments.jsonl`
  (une ligne JSON `{ "text": …, "source_file": … }` par segment). Source canonique pour la
  première exécution **et** les reprises (uniforme text/url/file).
- `documents` : `+ progress_segment INTEGER DEFAULT 0`, `+ progress_chunk INTEGER DEFAULT 0`.
- `rebuild_state(store, doc_id, embed_one) -> (EntityResolver, FactDeduper)` :
  - **deduper** : `store.load_fact_vectors(doc_id)` → `[(fact_id, vec)]` des faits **non-dup**
    (jointure `vec_facts` × `facts`), vecteurs désérialisés depuis le blob `sqlite-vec`
    (**format à confirmer à l'implémentation** : `numpy.frombuffer(blob, dtype='<f4').tolist()`) ;
    `FactDeduper` rempli via `add`.
  - **resolver** : `store.load_entities(doc_id)` → entités (`id, canonical_name, normalized_key,
    mention_count`) + vecteurs `vec_entities` ; `EntityResolver` avec `_by_key`, `_entities`,
    `_surface = Counter({canonical_name: mention_count})` (approximation suffisante : les nouvelles
    mentions s'ajoutent).
  - Les vecteurs stockés sont déjà L2-normalisés (l'embedder normalise avant écriture) → cosinus
    = produit scalaire cohérent.

> Reprise par chunk : le chunk interrompu (crash en plein milieu) n'a pas avancé `progress_chunk`
> (persisté seulement après un chunk **fini**) → il est re-traité entièrement ; ses faits partiels
> déjà en DB sont re-dédupliqués au rechargement. Sur-extraction possible **bornée à un chunk**.

## 6. `pipeline.py`

```
run_extraction(cfg, store, doc_id, segments, *, start_segment=0, start_chunk=0,
               resolver=None, deduper=None, client=None)
```
- Si `resolver`/`deduper` sont fournis → reprise (état pré-chargé) ; sinon construits vides
  (première exécution, comportement actuel).
- Boucle `for si, (text, source_file) in enumerate(segments)` puis `for ci, chunk in
  enumerate(chunk_text(text))`, en **sautant** tant que `(si, ci) < (start_segment, start_chunk)`.
- `round_end` enrichi : `{type:"round_end", segment: si, chunk: ci, source_file}`.
- Le pipeline **ne** persiste **pas** la progression et **ne** gère **pas** la pause (rôle du worker).
- `done`/`error` inchangés (fail-loud conservé).

## 7. `store.py`

- Schéma : `documents` + `progress_segment`, `progress_chunk`. (DB de dev jetable, sans migration :
  supprimer `data/nerve.db*` avant le smoke — cf. HANDOFF.)
- `init_db` : `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=5000` après l'ouverture.
- `set_status(doc_id, status)` ; `set_progress(doc_id, segment, chunk)`.
- `load_fact_vectors(doc_id) -> list[tuple[int, list[float]]]` (faits non-dup).
- `load_entities(doc_id) -> list[tuple[int, str, str, int, list[float]]]`
  (`id, canonical_name, normalized_key, mention_count, vec`).
- `list_resumable() -> list[int]` (statuts `running`/`queued`).
- `finish_document` existant (réutilisé pour `done`/`failed`).

## 8. `api.py`

- `lifespan` (`@asynccontextmanager`) : instancie `Scheduler(cfg, store)`, lance `_worker` comme
  tâche, appelle `reconcile()` ; au shutdown, annule proprement le worker. Exposer le scheduler
  via `app.state` (et/ou variable de module) pour les handlers.
- `POST /api/documents` (text|url) : crée le doc (`queued`), obtient les segments (text →
  `[(text,"")]` ; url → `transcode_url` → `[(md,"")]`, 422 si échec — déjà en place), écrit
  `segments.jsonl`, `scheduler.enqueue(doc_id)`, **retour immédiat** `{document_id, status:"queued"}`.
- `POST /api/documents/upload` : crée le doc (`queued`), `ingest_upload` → segments + `skipped`
  (422 si IngestError — déjà en place), écrit `segments.jsonl`, `enqueue`, retour
  `{document_id, status:"queued", skipped}`.
- `POST /api/documents/{id}/pause` → `scheduler.pause(id)` → méta. `…/resume` → `scheduler.resume(id)`.
- `GET /api/documents/{id}/events` (SSE, `StreamingResponse` `text/event-stream`) :
  `q = scheduler.subscribe(id)` ; **replay** `{type:"replay", facts: store.get_facts(id)}` ;
  `{type:"status", …}` ; boucle `await asyncio.wait_for(q.get(), timeout=15)` → `data: <json>` ;
  `: keepalive` sur timeout ; ferme sur `done`/`error` ; `unsubscribe` en `finally`.
- `GET /api/documents/{id}/facts` inchangé ; `GET /api/documents/{id}` (métadonnées) ajouté (léger).

## 9. `web/index.html`

POST → `{document_id}` → `const es = new EventSource('/api/documents/'+id+'/events')`.
`onmessage` : `replay` → `render(facts)` ; `fact` non-dup → ajout incrémental (Map nœuds/liens)
+ `render` ; `done`/`error` → `es.close()`. Garder l'échappement HTML existant (XSS). Rendu
soigné, pause/resume UI, navigation = **I-5** (ici : minimum pour ne pas régresser le live).

## 10. Tests (sans réseau)

- **`test_scheduler`** : worker traite un doc via un **fake `run_extraction`** (monkeypatch) →
  events émis dans l'ordre + statut `done` ; `pause` détectée sur `round_end` → statut `paused`
  + `set_progress` appelé, worker stoppé sans `done` ; `resume` → ré-enqueue et reprend au point
  persisté ; `reconcile` → un doc `running` en DB est ré-enqueué.
- **`test_rebuild_state`** : DB pré-remplie (faits non-dup + entités + vecteurs) → `rebuild_state`
  reconstruit deduper (un fait identique re-soumis est marqué doublon) et resolver (une entité de
  clé connue est réutilisée, pas recréée).
- **`test_pipeline`** (complément) : `start_segment`/`start_chunk` sautent les chunks attendus ;
  `round_end` porte `{segment, chunk}` ; reprise avec resolver/deduper fournis ne les recrée pas.
- **`test_store`** (complément) : `set_status`/`set_progress` ; round-trip `load_fact_vectors`/
  `load_entities` (vecteurs égaux à ε près) ; `list_resumable` ; WAL actif
  (`PRAGMA journal_mode` == `wal`).
- **`test_api`** (complément) : enqueue renvoie `status:"queued"` **immédiatement** (worker
  monkeypatché / non démarré) ; SSE `replay` + `fact` via `TestClient` (le fake worker émet
  `done` pour clore le flux) ; `pause`/`resume` renvoient le statut attendu.

Mocks : `run_extraction` et `embed`/`stream_chat` monkeypatchés ; aucun appel réseau ni Ollama.

## 11. Vérifications réelles (smoke, Ollama requis)

1. `rm -f data/nerve.db*` (changement de schéma : `progress_*`).
2. `uv run nerve` ; coller un texte → la visu se remplit **en direct** (SSE) ; le doc passe
   `queued`→`running`→`done`.
3. **Pause/reprise** : lancer un gros texte, `pause` en cours → statut `paused`, progression figée ;
   `resume` → reprend sans re-extraire les chunks déjà faits, pas de doublons aberrants.
4. **Reprise après crash** : tuer le serveur en cours d'extraction, le relancer → le doc reprend
   automatiquement (reconcile) au dernier chunk persisté.
5. **Concurrence** : ouvrir le SSE pendant l'extraction d'un autre doc → pas de blocage (WAL).

## 12. Risques / points à lever à l'implémentation

- **Désérialisation `sqlite-vec`** : confirmer que `SELECT embedding FROM vec_facts` rend le blob
  float32 relisible par `numpy.frombuffer(blob, dtype='<f4')` (sinon, re-embed des faits non-dup au
  rechargement — plus coûteux mais déterministe).
- **SSE sous `TestClient`** : le flux ne doit pas boucler indéfiniment en test → le fake worker émet
  `done` pour que le générateur sorte ; valider que `TestClient` lit bien un flux qui se termine.
- **Arrêt propre du worker** : au shutdown lifespan, annuler la tâche worker sans laisser une
  extraction à moitié écrite incohérente (le statut reste `running` → repris au prochain démarrage).
- **Course pause/round_end** : `_pause` est consulté par le worker entre deux chunks ; une pause
  arrivée pendant le dernier chunk est honorée au `round_end` suivant ou laisse le doc terminer s'il
  n'en reste aucun (acceptable).
