# nerve — Plan 2 / Incrément I-1 : qualité du graphe (fusion des nœuds + dedup de faits)

**Date** : 2026-06-13
**Statut** : design validé en brainstorming, en attente de relecture avant plan d'implémentation
**Pré-requis** : Plan 1 (walking skeleton) mergé dans `main` — pipeline texte → extraction → SQLite(+`sqlite-vec`) → force-graph.

## 0. Place dans le « Plan 2 »

Le périmètre reporté du Plan 1 regroupe **cinq sous-systèmes indépendants**. Plutôt qu'un seul
plan monolithique, on les traite en incréments, chacun avec son cycle spec → plan → implémentation :

| Incrément | Contenu | Dépend de |
|-----------|---------|-----------|
| **I-1 · Qualité du graphe** *(ce doc)* | embeddings + dedup `sqlite-vec` + fusion des entités | Plan 1 |
| I-2 · Ingestion | transcodeurs URL enfichables + zip/fichiers + upload | Plan 1 |
| I-3 · Scheduler + SSE live | file FIFO + pause/reprise + faits en direct + lot concurrence | Plan 1 |
| I-4 · Modèle 3-niveaux | navigation sets + transverse borné + recherche globale | **I-1** |
| I-5 · Visualisation complète | graphology, modes de couleur, étiquettes, thèmes | I-1, I-4 |

I-1 est choisi en premier : il corrige le problème de **variantes de nœuds** observé au smoke du
Plan 1 (`Cluny` vs `Cluny_Abbey`, `Saint-Gall` vs `Abbaye_de_Saint_Gall`), **remplit enfin la table
`vec_facts`** créée mais vide au Plan 1, et constitue le socle vectoriel de I-4 (transverse/recherche).

## 1. Objectif & périmètre de I-1

Transformer la sortie brute du LLM en un graphe **propre** : des nœuds où les variantes d'une même
entité fusionnent, et des arêtes où les faits redondants sont dédupliqués. Deux leviers
**complémentaires** :

- **(a) Fusion des nœuds** — un prompt de canonicalisation fort + une résolution d'entités
  (clé lexicale + garde hybride embedding/lexical), **intra-document**.
- **(b) Dedup de faits** — embeddings (bge-m3) + cosinus + seuil, port de la logique de l'original,
  adossé à `sqlite-vec`.

**Référence** : la logique provient de `../knowledge-graph-extractor/app.py`
(`embed_fact`, `check_duplicate`, `DEFAULT_PROMPT`). Constat clé issu de la lecture de l'original :
sa « normalisation des entités » est **portée par le prompt**, pas par du code ; sa dedup est au
**niveau fait** (triplet), pas entité. I-1 conserve cette logique et y **ajoute** un clustering
d'entités en code (décision utilisateur : approche « C »).

### Hors périmètre de I-1

- **Fusion/identité d'entités cross-document** (chaque document garde ses entités ; l'identité
  inter-documents est une affaire de *requête* dans I-4, via `vec_entities`/`vec_facts`, pas une fusion).
- **SSE live / scheduler** (I-3) : I-1 reste sur le modèle Plan 1 « rendu après complétion ».
- **Endpoints transverse/recherche** (I-4) : I-1 **remplit** `vec_facts`/`vec_entities` mais
  n'expose pas encore `/api/search` ni `/api/transverse`.
- **Visualisation avancée** (I-5).

## 2. Section A — Fusion des nœuds (approche C, rendue sûre)

### 2.1 Prompt de canonicalisation (baseline)

Porter dans `extract.py` les règles fortes du `DEFAULT_PROMPT` de l'original (adaptées au français
du corpus) : `subject`/`object` = **entités canoniques courtes**, jamais de prose ; retirer
articles/rôles/qualificatifs ; **réutiliser exactement la même chaîne** pour une même entité ;
narration/preuve dans `description`. Ce prompt remplace le `SYSTEM_PROMPT` plus faible du Plan 1
(qui produisait les underscores type `Cluny_Abbey`). À lui seul, il réduit déjà fortement les variantes.

### 2.2 Résolution d'entités (incrémentale, intra-document)

Nouveau module `entities.py`. Pour chaque `subject` et chaque `object` d'un fait entrant, dans le
contexte du document courant :

1. **Clé lexicale déterministe** : `casefold` + suppression des accents + remplacement de
   `[_\W]+` par une espace + espaces normalisés. **Match exact de clé → même nœud** (capte
   gratuitement `Cluny_Abbey` ↔ `Cluny Abbey` ↔ `cluny abbey`).
2. **Sinon, garde hybride embedding + lexical** : on embed le **nom d'entité** (bge-m3), KNN sur
   les entités déjà vues **du document** ; on **fusionne seulement si**
   `cosine ≥ ENTITY_THRESHOLD` **ET** un garde lexical passe (chevauchement de tokens normalisés
   au-dessus d'un minimum, **ou** une clé est sous-chaîne de l'autre, **ou** correspondance
   d'acronyme). Sinon → **nouvelle entité**. Le seuil d'embedding **seul ne décide jamais** d'une
   fusion : c'est ce garde qui coupe les faux positifs (`Notker le Bègue` vs `Notker le Chauve`).
3. **Label canonique** du nœud = forme de surface la **plus fréquente** (égalité → la plus courte) ;
   `mention_count` suit la fréquence.

Interface : `EntityResolver(store, document_id, embed_fn, threshold)` →
`resolve(name: str) -> entity_id` (effets : crée/maj l'entité, persiste son embedding la 1re fois).
`embed_fn` est injectable (vraie fonction bge-m3 en prod ; factice déterministe en test).

## 3. Section B — Client embeddings & dedup de faits

### 3.1 `embeddings.py`

Client **OpenAI-compatible** `/v1/embeddings` en `httpx` (même style que `llm.py`) :
`embed(texts: list[str]) -> list[list[float]]`, basé sur `cfg.embed` (`base_url`/`api_key`/`model`)
et `embed_dim`. Vecteurs **L2-normalisés** (cosinus = produit scalaire, cohérent `sqlite-vec`).
Supporte le batch. Erreur réseau / endpoint absent → exception (pas de retour silencieux).

### 3.2 `dedup.py`

Port de l'original, adossé aux embeddings :

- `dedup_text(fact, field="triple")` : `field` configurable (`triple` = `"subject predicate object"`,
  ou `title`, `description`, `title+desc`, …), défaut `triple`, stocké dans `documents.params_json`.
- Pour chaque fait : embed de son `dedup_text`, cosinus contre les **faits non-dup déjà retenus du
  même document** ; si `≥ DEDUP_THRESHOLD` → `is_duplicate=1`, `dup_of_id` = le plus proche ;
  sinon retenu.

### 3.3 Stockage vectoriel

Pendant une extraction (**= un document, borné à des centaines de faits**), les **working sets sont
tenus en mémoire** (cosinus `numpy`, rapide — mirroir de l'original) : un pour les faits non-dup
(dedup), un pour les entités du document (résolution de la Section A). **En parallèle**, on
**persiste** l'embedding de chaque fait non-dup dans `vec_facts` et de chaque entité dans
`vec_entities`. Cette persistance ne sert **pas** à la dedup/résolution intra-document (faites en
mémoire) mais à la **recherche globale et au transverse de I-4**. `vec_facts`, créée vide au Plan 1,
est enfin remplie.

## 4. Modèle de données (deltas Plan 1)

```sql
-- documents : compteurs de qualité (déjà prévus au design global §4)
ALTER TABLE documents ADD COLUMN unique_facts    INTEGER DEFAULT 0;
ALTER TABLE documents ADD COLUMN duplicate_facts INTEGER DEFAULT 0;

-- facts : rattachement aux entités résolues
ALTER TABLE facts ADD COLUMN subject_entity_id INTEGER REFERENCES entities(id);
ALTER TABLE facts ADD COLUMN object_entity_id  INTEGER REFERENCES entities(id);

-- entités résolues, par document
CREATE TABLE entities (
  id INTEGER PRIMARY KEY,
  document_id INTEGER REFERENCES documents(id),
  canonical_name TEXT NOT NULL,
  normalized_key TEXT NOT NULL,
  mention_count INTEGER DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_entities_doc_key ON entities(document_id, normalized_key);

-- index vectoriel des entités (dim figée = embed_dim)
CREATE VIRTUAL TABLE vec_entities USING vec0(
  entity_id INTEGER PRIMARY KEY, embedding FLOAT[EMBED_DIM]
);

-- vec_facts (créée vide au Plan 1) : désormais alimentée, 1 ligne / fait non-dup
```

En pratique, I-1 **met à jour le `SCHEMA` de `store.py`** (DB de dev jetable, `data/` gitignoré) —
**pas de framework de migration** ; les `ALTER` ci-dessus ne font que **décrire les deltas** par
rapport au schéma Plan 1.

Le **graphe rendu** n'affiche que les faits **non-dup** ; les nœuds portent `canonical_name` (via
`subject_entity_id`/`object_entity_id`). Les compteurs `total/unique/duplicate` sont remontés par l'API.

## 5. Flux pipeline (I-1)

Toujours « rendu après complétion » (le live SSE est I-3). Pour chaque fait parsé :

1. `subject_entity_id = resolver.resolve(subject)` ; `object_entity_id = resolver.resolve(object)`
   (Section A : clé lexicale → garde hybride → fusion ou nouvelle entité ; persistance `vec_entities`).
2. `is_dup, dup_of = dedup.check(fact)` sur le working set du document (Section B).
3. `store.add_fact(...)` avec `is_duplicate`, `dup_of_id`, `subject_entity_id`, `object_entity_id` ;
   maj `total/unique/duplicate_facts`.
4. Si non-dup : ajouter l'embedding au working set **et** à `vec_facts`.

## 6. Section C — Calibration, tests, erreurs

### 6.1 Calibration des seuils (bge-m3)

Deux seuils **distincts** et configurables (env) : `ENTITY_THRESHOLD` (fusion de nœuds, plutôt
conservateur) et `DEDUP_THRESHOLD` (dedup de faits). L'ancien `0.90` était calé sur jina et **ne
s'applique pas tel quel**. Le plan inclura un **script/test de calibration** : sur un petit jeu
**étiqueté** de paires (doublons connus / distincts connus, tirés de cas réels — Cluny/Saint-Gall),
il affiche la distribution des cosinus **contre bge-m3 réel** pour choisir le point de coupure qui
sépare le mieux. Les défauts livrés sont des **valeurs de départ explicitement à calibrer** (smoke
manuel, comme la Task 10 du Plan 1), pas des constantes définitives.

### 6.2 Tests (sans Ollama, comme au Plan 1)

- `embeddings.py` : `httpx.MockTransport` (réponse `/v1/embeddings` simulée).
- `entities.py` + `dedup.py` : **fonction d'embedding factice déterministe** (vecteurs contrôlés)
  pour tester la logique sans réseau —
  - la clé lexicale regroupe `Cluny_Abbey` / `Cluny Abbey` / `cluny abbey` ;
  - deux faits sémantiquement équivalents fusionnent (dedup), deux distincts non ;
  - **le garde hybride bloque un faux positif** : cosinus élevé mais garde lexical KO → **pas** de fusion ;
  - le label canonique = forme la plus fréquente.
- `store.py` : entités/`vec_entities` créées, compteurs corrects.
- **Smoke réel** contre bge-m3 (Cluny/Saint-Gall) : les variantes fusionnent ; valide aussi le
  réglage des seuils.

### 6.3 Gestion d'erreurs (fail loud)

La dedup et la résolution d'entités **exigent** le provider d'embeddings. S'il est injoignable ou
n'expose pas `/v1/embeddings` (cas OpenRouter), on **échoue le document avec une erreur claire**
(comme le durcissement `llm.py` du Plan 1) — jamais de dedup silencieusement partielle. Prérequis
setup : `ollama pull bge-m3`.

## 7. Modules touchés

```
nerve/
  embeddings.py   # NOUVEAU — client /v1/embeddings (httpx), vecteurs normalisés
  entities.py     # NOUVEAU — résolution d'entités (clé lexicale + garde hybride + registre)
  dedup.py        # NOUVEAU — dedup de faits (embed + cosinus + seuil) sur working set
  extract.py      # MODIF — prompt de canonicalisation fort (remplace le SYSTEM_PROMPT Plan 1)
  store.py        # MODIF — schéma (entities, vec_entities, colonnes), CRUD entités, remplit vec_facts
  pipeline.py     # MODIF — intègre résolution d'entités + dedup + persistance vectorielle
  config.py       # MODIF — ENTITY_THRESHOLD, DEDUP_THRESHOLD, DEDUP_FIELD
  api.py          # MODIF — remonte total/unique/duplicate ; n'expose que les faits non-dup
```

`numpy` (déclaré dès le Plan 1) est enfin utilisé (cosinus du working set). Architecture : ajout de
`entities.py` par rapport au design global §5 — responsabilité distincte de `dedup.py` (fusion de
**nœuds** vs dedup de **faits**), testable isolément.

## 8. Risques & points à vérifier (ne pas présumer)

- **bge-m3 sur noms courts** : la similarité d'embedding de noms d'entités très courts est bruitée
  → d'où le **garde lexical obligatoire** ; valider que le couple (seuil + garde) sépare bien sur le
  corpus réel.
- **Composition seuils** : `ENTITY_THRESHOLD` et `DEDUP_THRESHOLD` se règlent indépendamment ;
  documenter les valeurs retenues après calibration.
- **Dimension figée** : `vec_facts`/`vec_entities` fixent `EMBED_DIM` (bge-m3 = 1024). Changer de
  modèle ⇒ réindexer.
- **Coût d'embedding** : un appel par entité distincte + un par fait ; rester en batch quand possible.
- **Provider d'embeddings absent** chez certains LLM distants (OpenRouter) → géré par la config
  séparée LLM/embeddings (on peut faire LLM distant + embeddings Ollama locaux).

## 9. Configuration (ajouts)

```
EMBED_BASE_URL=http://localhost:11434/v1  EMBED_API_KEY=ollama  EMBED_MODEL=bge-m3  EMBED_DIM=1024
ENTITY_THRESHOLD=0.80   # fusion de nœuds (conservateur) — défaut de DÉPART, à calibrer (§6.1)
DEDUP_THRESHOLD=0.85    # dedup de faits — défaut de DÉPART, à calibrer (§6.1)
DEDUP_FIELD=triple      # triple|title|description|title+desc|all
```
