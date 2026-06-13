# nerve — Plan 2 / I-4 · Modèle 3-niveaux (navigation sets + transverse + recherche)

**Date** : 2026-06-14
**Statut** : design validé en brainstorming, en attente de relecture avant plan d'implémentation
**Incrément** : I-4 (dépend de **I-1** pour `vec_facts`/`vec_entities` et la résolution d'entités ; I-1/I-2/I-3 mergés). Branche : `nerve-i4-3levels`.
**Sources de cadrage** : spec globale §1 (trois niveaux de navigation), §4 (modèle de données), §7 (API « Transverse & recherche ») ; HANDOFF Plan 2 §3 (I-4).

## 1. But

Donner accès aux **trois niveaux de navigation** du produit et **consommer** les index vectoriels remplis mais restés *write-only* depuis I-1 :

- **Document** : graphe d'un document (déjà servi par `/api/documents/{id}/facts` + flux SSE live).
- **Source Set** : graphe agrégé d'un corpus (tous les documents d'un set), nœuds canoniques.
- **Subset transverse** : sous-graphe **borné** à travers plusieurs sets, construit à la requête autour d'une entité (identité par clé normalisée + voisinage sémantique `vec_entities`).
- **Recherche globale** : recherche sémantique sur `vec_facts`, tous documents confondus.

Plus une **UI minimale** rendant ces niveaux utilisables de bout en bout (le rendu riche reste I-5).

## 2. Périmètre

**Dans I-4**
- Module `graph.py` : assemblage pur `{nodes, links}` à partir de faits enrichis.
- `store.py` : requêtes **lecture seule** pour sets, graphe de set, recherche `vec_facts`, transverse (`vec_entities` + clé normalisée).
- `api.py` : `GET /api/sets`, `GET /api/sets/{id}`, `GET /api/sets/{id}/graph`, `GET /api/search`, `GET /api/transverse`.
- `web/index.html` : UI minimale (liste des sets → documents → graphe ; barre de recherche ; champ transverse). Flux « coller texte → SSE live » **conservé**.

**Hors I-4 (→ I-5)**
- Visualisation riche : graphology (louvain/centralité), modes de couleur, étiquettes d'arêtes, cartes de fait, thèmes clair/sombre.
- **Fusion/dedup automatique cross-document** (le transverse reste une *vue*, pas un merge persistant — cohérent I-1).
- Rendu « corpus entier d'un bloc ». Recherche renvoyant un graphe (v1 = liste de faits classés).

## 3. Décisions arrêtées (brainstorming)

1. **Périmètre = backend + UI minimale** (incrément utilisable de bout en bout ; empiète volontairement et a minima sur I-5 pour la navigation).
2. **Liaison transverse = clé normalisée + voisinage vectoriel** :
   - **Identité** cross-document = même `entities.normalized_key` → une *vue* qui collapse les occurrences (aucun merge persistant en DB, cohérent avec la décision I-1 « pas de fusion cross-document »).
   - **Voisinage/découverte** = top-k `vec_entities` (entités sémantiquement proches) pour enrichir le sous-graphe, borné par k + filtres.
   - Déterministe pour l'identité, sémantique pour la découverte ; consomme enfin `vec_entities`.
3. **Identité de nœud uniforme = `normalized_key`** aux niveaux Set et Transverse (le même nom d'entité dans plusieurs documents d'un set ⇒ un seul nœud).
4. **Recherche v1 = liste de faits classés** (pas de graphe), sur `vec_facts`.

## 4. `graph.py` (nouveau module, pur)

Assemblage sans accès DB ni réseau → testable isolément.

```
build_graph(rows) -> {"nodes": [...], "links": [...]}
```

- `rows` = faits **non-dup** enrichis : chaque ligne porte au moins
  `(s_key, s_name, predicate, o_key, o_name, fact_id, confidence, document_id)`
  où `*_key` = `normalized_key` de l'extrémité et `*_name` = `canonical_name`.
- **Nœud** : identité = `normalized_key` ; champs `{id: key, label: name, mentions}`. Si plusieurs `canonical_name` pour une même clé (variantes cross-doc), retenir le libellé de l'occurrence la **plus mentionnée** (sinon, le premier rencontré — déterministe par tri).
- **Lien** : `{source: s_key, target: o_key, predicate, fact_id}`, **dédupliqué** par `(s_key, predicate, o_key)` (premier gagnant).
- Les faits dont une extrémité n'a pas de clé d'entité sont **ignorés** par `build_graph` (pas de nœud canonique) — décision v1, cf. §11 (repli sur le texte brut rejeté car il casserait le collapse par clé).

## 5. `store.py` (nouvelles requêtes, lecture seule)

- `list_sets() -> [{id, name, description, document_count}]` — agrège le nombre de documents par set.
- `get_set(set_id) -> {set..., documents: [métadonnées]}` (ou `None`).
- `facts_for_set(set_id, min_conf=None) -> rows` — faits non-dup des documents du set, **joints aux entités** (sujet/objet → `normalized_key`, `canonical_name`, `mention_count`), filtre `confidence >= min_conf` si fourni. Forme = celle attendue par `build_graph` (§4).
- `search_facts(query_vec, k, sets=None) -> [{fact..., document_id, set_id, distance}]` — KNN sur `vec_facts`, filtre optionnel par `set_id` (jointure `facts → documents`). Voir §11 pour la combinaison KNN + filtre.
- Transverse :
  - `entities_by_key(norm_key, sets=None) -> [entity rows]` — toutes les occurrences cross-document d'une clé (filtre sets optionnel).
  - `entity_neighbors(query_vec, k, sets=None) -> [(entity_id, normalized_key, canonical_name, distance)]` — KNN sur `vec_entities` (filtre sets optionnel).
  - `facts_for_entities(entity_ids, min_conf=None) -> rows` — faits non-dup touchant (sujet **ou** objet) l'un des entity_ids, enrichis comme `facts_for_set`.

Toutes ces requêtes réutilisent `WAL`/`busy_timeout` déjà en place (I-3) ; aucune écriture.

## 6. `api.py` (nouveaux endpoints)

- `GET /api/sets` → `[{id, name, document_count}]`.
- `GET /api/sets/{id}` → détail + documents ; **404** si inconnu.
- `GET /api/sets/{id}/graph?min_conf=` → `{nodes, links}` via `facts_for_set` + `build_graph` ; **404** si set inconnu (graphe vide si set sans faits).
- `GET /api/search?q=&sets=&k=` → faits classés. `q` requis (**400** sinon) ; `sets` = liste optionnelle d'ids ; `k` défaut 20. Embedding de `q` via le provider configuré (`embed`).
- `GET /api/transverse?entity=&sets=&min_conf=&k=` → `{nodes, links}` (§7). `entity` requis (**400** sinon) ; `k` défaut 10 (taille du voisinage `vec_entities`).

`sets` est passé en query répétée ou CSV (tranché au plan ; cohérent avec FastAPI). Les handlers qui appellent l'embedding sont `async def` (cohérent I-3 : pas de handler sync mutant l'état).

## 7. Flux transverse (le cœur)

`GET /api/transverse?entity=&sets=&min_conf=&k=` :

1. **Normaliser** `entity` avec la **même normalisation** que la résolution d'entités (fonction de `entities.py` utilisée pour calculer `normalized_key`) → `norm_key`.
2. `entities_by_key(norm_key, sets)` → occurrences cross-document (le **nœud central** ; vide ⇒ graphe vide, pas une erreur).
3. `facts_for_entities([occurrences], min_conf)` → faits **1-hop** ; les autres extrémités deviennent des nœuds voisins.
4. **Embed** `entity` → `entity_neighbors(query_vec, k, sets)` → entités sémantiquement proches ; ajouter leurs occurrences au set de nœuds, puis `facts_for_entities` sur l'ensemble pour récupérer les faits **reliant** ces nœuds.
5. `build_graph(rows)` → `{nodes, links}` borné (l'union des faits 1-hop + faits du voisinage, dédupliqués par `build_graph`).

Bornage : le sous-graphe est intrinsèquement borné par `entities_by_key` (occurrences d'une seule clé) + `k` voisins sémantiques + `min_conf`. Pas de rendu « tout le corpus ».

## 8. `web/index.html` (UI minimale)

Réutilise le canvas `force-graph` existant et la fonction `addFact`/`build_graph` côté front (rendu `{nodes, links}`).

- **Barre latérale** : `GET /api/sets` → liste cliquable. Clic set → `GET /api/sets/{id}` (documents) + bouton « graphe du set » (`/sets/{id}/graph`). Clic document → graphe du document (faits existants).
- **Recherche** : champ `q` → `GET /api/search` → liste de faits classés (sujet · prédicat · objet, score, document/set), cliquables.
- **Transverse** : champ entité (+ filtres sets/min_conf) → `GET /api/transverse` → rendu graphe.
- Le flux existant **« coller texte → SSE live »** est conservé (onglet/section dédiée).

Minimal et fonctionnel : pas de thèmes, pas de clustering, pas de cartes de fait (I-5).

## 9. Tests (sans réseau, `monkeypatch` sur l'embedding)

- `graph.py` : assemblage, **dédup des liens**, **collapse cross-document** par clé, choix du libellé le plus mentionné, exclusion des faits sans clé.
- `store.py` : `list_sets`/`get_set` ; `facts_for_set` (forme + filtre `min_conf`) ; `search_facts` (KNN + filtre `sets`, vecteurs injectés) ; `entities_by_key`, `entity_neighbors`, `facts_for_entities`.
- `api.py` (TestClient + `importlib.reload`) : sets (liste/détail/404), set-graph, search (400 sans `q`, classement, filtre sets), transverse (400 sans `entity`, identité cross-doc, enrichissement voisinage, entité inconnue → graphe vide).
- Conventions reprises : `asyncio_mode=auto`, `TestClient` + `importlib.reload(api)` après `setenv`, monkeypatch `embed`.

## 10. Vérifications réelles (smoke, Ollama requis)

`rm -f data/nerve.db*` (schéma I-3 inchangé ici, mais repartir propre), puis sur DB réelle (qwen3.6 + bge-m3) :
1. Créer **2 sets**, ingérer **plusieurs documents** partageant une entité (ex. « Cluny »).
2. `GET /api/sets/{id}/graph` : le graphe du set collapse l'entité partagée en **un seul nœud**.
3. `GET /api/search?q=…` : faits pertinents classés par similarité, filtre `sets` respecté.
4. `GET /api/transverse?entity=Cluny&sets=…` : sous-graphe reliant les occurrences cross-document + voisins sémantiques `vec_entities`, borné.

## 11. Risques / points à lever à l'implémentation

- **KNN `sqlite-vec` + filtre `sets`** : le filtrage par `set_id` après le KNN peut sous-retourner (les k plus proches peuvent être hors filtre). Mitigation par défaut : **sur-échantillonner** (KNN sur `k' > k`, p. ex. `k * 5` ou un plancher), joindre `facts/entities → documents`, filtrer `set_id`, puis tronquer à `k`. Vérifier la syntaxe KNN réellement supportée (`embedding MATCH ? … LIMIT ?` vs `AND k = ?`) au plan.
- **Faits sans `subject_entity_id`/`object_entity_id`** : en pratique la résolution I-1 assigne toujours sujet/objet, mais des faits anciens/limites peuvent manquer une extrémité. Décision par défaut : ces faits sont **exclus** du graphe (pas de nœud canonique). À confirmer au plan (repli sur le texte brut `subject`/`object` rejeté en v1 — casserait le collapse par clé).
- **Normalisation de la requête transverse** : doit appeler **exactement** la fonction de `entities.py` qui produit `normalized_key` (pas une ré-implémentation) — sinon désalignement clé requête / clé stockée.
- **Performance** : corpus local mono-user → volumes modestes ; pas d'optimisation spéculative (YAGNI). Index existants : `idx_facts_doc`, `idx_entities_doc_key`.
