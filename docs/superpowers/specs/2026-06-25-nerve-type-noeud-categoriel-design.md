# Spec — Type de nœud catégoriel (issue #11)

**Date** : 2026-06-25
**Branche** : `feat-11-type-noeud-categoriel` (off `main`, incrément indépendant)
**Statut** : design approuvé, en attente de relecture avant plan d'implémentation

## Contexte & objectif

`nerve` étiquette chaque nœud du graphe par un type **binaire** `entity | value`
(colonne `entities.kind`, posée par le LLM à l'extraction, coloration « Type » au front).
Le binaire colore grossièrement : entités vs valeurs littérales. Objectif de #11 : passer à
un **enum de catégories** pour colorer le graphe plus finement et porter plus de sens.

Le risque connu (cf. issue) est la **cohérence des classes produites par le LLM** : une même
entité peut être étiquetée différemment d'un fait à l'autre. La résolution choisie est le
**vote majoritaire** (voir Décisions).

## Décisions verrouillées (brainstorming)

1. **Taxonomie : 6 catégories.** Identifiants stables, minuscules sans accent (valeurs
   persistées et échangées schéma↔store↔front) : `personne`, `lieu`, `organisation`,
   `concept`, `date`, `quantite`. `concept` est la catégorie de **repli** pour l'abstrait
   ou l'ambigu. Le binaire actuel disparaît : `date`/`quantite` reprennent l'ancien rôle de
   `value`, le reste celui de `entity`.
2. **Résolution = vote majoritaire, strict aux deux niveaux.**
   - **Intra-document** : à chaque occurrence d'une entité, on vote pour la catégorie vue ;
     `kind` = catégorie majoritaire (`argmax` des votes).
   - **Cross-document (collapse)** : `build_graph` somme les votes des entités-docs de même
     clé normalisée puis prend l'`argmax`.
   - Tie-break déterministe : **ordre fixe de la taxonomie** ci-dessus.
3. **Changement de schéma assumé → re-ingestion.** `entities.kind` change de domaine et une
   colonne `kind_votes` apparaît : DB de dev recréée (`rm -f data/nerve.db*`, base jetable,
   pas de migration). Sans incidence sur les tests (bases temporaires).
4. **Front : coloration enrichie + légende only.** Pas de filtre par catégorie (YAGNI, hors
   issue). Réutilise la palette daltonien-safe (Okabe-Ito) déjà en place.

## Architecture & composants

### 1. Schéma & store (`nerve/store.py`)
- `entities.kind` : domaine `'entity'|'value'` → les 6 identifiants ; `DEFAULT 'concept'`
  (repli neutre ; en pratique toujours posé explicitement par le pipeline).
- Nouvelle colonne `entities.kind_votes TEXT` — `Counter` sérialisé en JSON
  (ex. `{"personne":3,"concept":1}`), cohérent avec `tags_json` / `params_json` déjà en JSON.
- `create_entity(..., kind)` : initialise `kind_votes = {kind: 1}` et `kind = kind`.
- **`vote_entity_kind(entity_id, categorie)`** remplace `promote_entity_kind` :
  charge `kind_votes`, `kind_votes[categorie] += 1`, recalcule `kind = argmax`
  (tie-break = ordre de la taxonomie), persiste les deux colonnes.
- `_GRAPH_COLS` expose **en interne** `se.id AS s_entity_id`, `oe.id AS o_entity_id`,
  `se.kind_votes AS s_votes`, `oe.kind_votes AS o_votes` (en plus de `s_kind`/`o_kind`).
  Ces colonnes sont **consommées par `build_graph` puis jetées** : le client ne reçoit que
  `{nodes, links}`, donc aucune fuite d'`entity_id` (cohérent avec #9, qui visait `get_facts`).

### 2. Extraction (`nerve/extract.py`)
- `FACT_SCHEMA` : `subject_kind` / `object_kind` passent à `"enum": [<6 identifiants>]`.
  Schéma strict conservé (`additionalProperties: false`, tous champs requis, cf. #18).
- `SYSTEM_PROMPT` : remplacer le paragraphe `entity|value` par les 6 catégories, **chacune
  avec une définition courte + un exemple**, et une **règle de repli explicite** : « en cas de
  doute ou pour un concept abstrait → `concept` ». Conserver « N'omets JAMAIS subject_kind ni
  object_kind » et l'énumération des champs par fait.

### 3. Pipeline (`nerve/pipeline.py`)
- `_kind(raw)` : normaliser vers l'enum (minuscules, trim) ; toute valeur hors des 6 →
  repli `concept` (filet, même sous schéma strict — le LLM peut déroger). `resolve()` reçoit
  donc toujours une catégorie valide.

### 4. Résolution (`nerve/entities.py`)
- `resolve(name, kind)` vote **à chaque occurrence** (création ET ré-identification) via
  `vote_entity_kind`, au lieu de l'actuel one-way `value→entity`. Le registre mémoire est
  inchangé ; seul l'appel de promotion devient un vote.

### 5. Collapse (`nerve/graph.py`)
- `build_graph` : pour chaque clé de nœud, maintenir `{entity_id → Counter}` (dédup des
  occurrences-faits d'une même entité-doc), **sommer** les Counters, `kind = argmax`
  (tie-break = ordre de la taxonomie). `_add_node` cesse d'appliquer « entity domine value » ;
  `entity_id` / `kind_votes` ne sont **pas** recopiés dans les nœuds de sortie.

### 6. Front (`nerve/web/graph.js`, `index.html`, `theme.css`)
- Palette : ajouter `cat` aux jeux de couleurs renvoyés par `cc()` — un mapping
  catégorie→couleur fixe sur 6 teintes Okabe-Ito daltonien-safe (réutilise `comm[]`).
- `nodeColor`, mode `"type"` : `cc().cat[n.kind]` (fallback `cc().node` si catégorie absente).
- `renderLegend`, mode Type : 6 lignes (Personne / Lieu / Organisation / Concept / Date /
  Quantité), libellés FR jolis mappés depuis les identifiants.

## Flux

```
extraction d'un fait → subject_kind/object_kind ∈ 6 catégories (prompt + schéma strict)
  → pipeline._kind() normalise/replie → resolve(name, categorie)
  → vote_entity_kind : kind_votes[cat]++ ; entities.kind = argmax (intra-doc)
lecture graphe (set/transverse) → _GRAPH_COLS expose kind_votes + entity_id (interne)
  → build_graph somme les votes par clé (dédup entity_id) → kind du nœud = argmax (cross-doc)
  → {nodes, links} au client (sans entity_id/votes)
front mode « Type » → cc().cat[kind] + légende 6 catégories
```

## Gestion d'erreur (fail-loud, sans masquer)
- LLM renvoyant une catégorie hors enum malgré le schéma strict → `_kind()` replie sur
  `concept` (filet documenté, pas un masquage : le champ reste rempli et déterministe).
- `kind_votes` absent/illisible pour une entité (base incohérente) → `vote_entity_kind`
  **lève** plutôt que de réinitialiser silencieusement (fail-loud ; la DB est recréée au
  changement de schéma, l'état incohérent ne doit pas arriver).

## Tests (hermétiques, sans réseau — TDD)
`tests/` (un fichier par module touché) :
- `store` : `create_entity` initialise `kind_votes` ; `vote_entity_kind` — majorité simple,
  bascule du `kind` quand une catégorie prend la tête, **tie-break** déterministe à égalité ;
  `_GRAPH_COLS` expose `s_votes`/`o_votes`.
- `extract` : `FACT_SCHEMA` porte l'enum à 6 valeurs ; le format de réponse reste strict.
- `pipeline` : `_kind()` normalise les 6 catégories et replie l'inconnu sur `concept`.
- `entities` : `resolve` répété sur la même entité avec catégories mêlées → `kind` = majoritaire.
- `graph` : `build_graph` collapse cross-document → `kind` = argmax des votes sommés ;
  dédup par `entity_id` (une entité vue dans N faits ne compte qu'une fois) ;
  `entity_id`/`kind_votes` absents des nœuds de sortie.
- Front : vérifié par harness/smoke (palette `cat`, légende 6 lignes), comme #13.

## Hors périmètre
- Filtre/masquage du graphe par catégorie.
- Sous-catégories ou taxonomie hiérarchique.
- Reclassement rétroactif d'une base existante (re-ingestion à la place).
- Catégorisation des **valeurs** au-delà de `date`/`quantite` (p. ex. devise, pourcentage).

## Workflow
Branche `feat-11-type-noeud-categoriel` → PR vers `main`, revue `gemini-code-assist[bot]`.
Changement de schéma → `rm -f data/nerve.db*` avant smoke réel. Exécution subagent-driven
*lean* (1 sous-agent implémenteur par tâche TDD), conformément au workflow du projet.
