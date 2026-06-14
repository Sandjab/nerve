# Plan 2 / I-5 · Visualisation complète (graphology) — Design

> Spec d'incrément. Dépend de I-1 (entités/embeddings) et I-4 (endpoints sets/graphe/transverse).
> Dernier incrément du Plan 2. Source de périmètre : spec global §3 (visualisation).

## 1. Objectif

Donner à la vue graphe une **couche analytique et esthétique complète** : communautés (louvain) et centralité calculées côté client via **graphology**, **modes de coloration** sémantiques togglables (communauté · source set · type · uniforme), encodage de la **confiance** sur les arêtes, mise en évidence des **passerelles inter-sources** (bordeaux), **étiquettes d'arêtes** et **chemin le plus long** togglables, **cartes de fait** au survol, **thèmes clair/sombre**. Le rendu reste **force-graph** (Sigma tenu en réserve, même couche graphology).

Choix structurant acté au brainstorming : la coloration **par type (entité vs valeur)** s'appuie sur un **vrai type stocké** (étiqueté par le LLM), pas une heuristique. I-5 touche donc aussi l'extraction et le schéma — c'est un incrément « données + front », pas purement front.

## 2. Décisions actées (ne pas relitiger)

- **Type de nœud = binaire** `entity` | `value` (fidèle au spec ; montée en catégories possible plus tard sans blocage).
- **Type réel, pas heuristique** : le LLM étiquette chaque sujet/objet ; persisté en colonne `entities.kind`.
- **Migration** : DB de dev jetable, sans framework de migration → colonne ajoutée au DDL + **re-ingestion** (`rm -f data/nerve.db*`), comme à chaque incrément touchant le schéma (Plan 1 / I-1 / I-2 / I-3). Aucun code de migration.
- **Conflit de `kind`** à la résolution d'entités : `entity` **domine** `value` (un nœud jamais étiqueté « valeur » reste « entité ») ; déterministe, validé au smoke.
- **Analytics côté client** : louvain + centralité calculés par graphology sur le graphe courant — **aucun nouvel appel serveur**, aucun endpoint ajouté.
- **`is_bridge`** (passerelle) = arête **incidente à un nœud hub multi-sets** (`len(node.sets) > 1`) — ce sont les nœuds collapsés cross-document qui matérialisent le transverse.
- **Structure de fichiers** : extraction du JS/CSS inline de `index.html` vers `web/graph.js` + `web/theme.css` (spec global §file-structure) ; servis par routes `FileResponse` symétriques à `/`.
- **Chemin le plus long** : inclus, version simple (toggle, sans sélection de nœuds — repris de l'original).
- **Périmètre clos à I-5** (dernier incrément du Plan 2).

## 3. Architecture (deux couches)

```
Couche données (backend, nouvelle à cause du type réel)
  extract.py   → schéma JSON + prompt : subject_kind / object_kind ∈ {entity, value}
  store.py     → DDL: entities.kind ; create_entity(kind=…) ; _GRAPH_COLS += s_kind/o_kind/set_id
  entities.py  → le resolver propage/agrège kind (entity domine value)
  pipeline.py  → câble kind du fait → résolution
  graph.py     → build_graph enrichit nœuds (kind, sets) et liens (confidence, is_bridge)
  api.py       → routes FileResponse /graph.js et /theme.css (serving statique)

Couche visualisation (front)
  web/index.html → markup minimal + barre de contrôles + légende + liens vers graph.js/theme.css
  web/graph.js   → force-graph (rendu) + graphology (louvain/centralité) + modes couleur/taille
                   + arêtes (confiance, passerelles, étiquettes) + cartes de fait + chemin + thème
  web/theme.css  → palette scriptorium, thèmes clair + sombre (variables CSS)
```

Le contrat de données entre les deux couches = la forme `{nodes, links}` enrichie (§7). Les endpoints I-4 (`/api/sets/{id}/graph`, `/api/transverse`) sont **inchangés** : seul leur producteur `build_graph` enrichit la charge utile.

## 4. Schéma & migration

- `entities` gagne `kind TEXT` (valeurs `'entity'` | `'value'` ; `NULL` toléré pour d'anciens enregistrements, traité comme `'entity'` côté front par défaut).
- Ajout au DDL `CREATE TABLE … entities (…)` ; pas de `ALTER`. Les bases existantes (dev) sont recréées par `rm -f data/nerve.db*` puis re-ingestion. Convention projet établie (handoff §2 : `CREATE TABLE IF NOT EXISTS` n'ajoute pas de colonnes).
- `vec_facts` / `vec_entities` : **inchangés** (I-5 ne touche pas les embeddings).

## 5. Extraction (`extract.py`) — type entité/valeur

- Le schéma JSON de sortie LLM gagne, **par fait**, `subject_kind` et `object_kind` (enum `entity` | `value`).
- Le prompt explicite la distinction : *entité nommée* (personne, lieu, organisation, ouvrage, concept réifié) vs *valeur littérale* (date, nombre, mesure, durée, quantité, proportion). Une poignée d'exemples.
- Rétro-compatibilité du parseur : si le LLM omet le champ, défaut `entity` (fail-soft sur ce champ précis uniquement ; le reste de l'extraction garde son fail-loud).

## 6. Résolution d'entités (`entities.py` / `store.py` / `pipeline.py`)

- `store.create_entity(document_id, canonical_name, normalized_key, kind="entity")` : nouvel argument `kind`, écrit dans la colonne.
- Le resolver reçoit le `kind` du sujet/objet depuis le fait et le transmet à la création/fusion d'entité.
- **Politique de conflit** (même `normalized_key`, `kind` divergents) : `entity` domine `value`. Concrètement, si une occurrence existante est `value` et qu'une nouvelle est `entity`, la promotion vers `entity` est appliquée ; jamais l'inverse. (Cas rare : un littéral et une entité partageant une clé normalisée.)
- Pas de changement aux seuils ni à la garde de fusion (I-1).

## 7. Payload `build_graph` enrichi (`graph.py` + `_GRAPH_COLS`)

`_GRAPH_COLS` expose en plus : `se.kind AS s_kind`, `oe.kind AS o_kind`, et `d.set_id AS set_id`. Les requêtes qui consomment `_GRAPH_COLS` (`facts_for_set`, `facts_for_entities`) joignent déjà `documents` (ou l'ajoutent : `facts_for_entities` doit joindre `documents` pour `set_id`).

`build_graph(rows)` produit :

- **nœuds** : `{id, label, mentions, kind, sets}` où
  - `kind` = type du nœud, agrégé sur ses occurrences avec `entity` dominant `value` (un nœud vu au moins une fois `entity` est `entity`) ;
  - `sets` = **liste triée des `set_id` distincts** où la clé apparaît (sert à la couleur par set et à la détection de hub).
- **liens** : `{source, target, predicate, fact_id, confidence, is_bridge}` où
  - `confidence` = confiance du fait (déjà dans `_GRAPH_COLS`, désormais propagée) ;
  - `is_bridge` = `True` si `len(source.sets) > 1` **ou** `len(target.sets) > 1` (incident à un hub multi-sets).

`build_graph` reste **pur** (aucune dépendance DB/réseau). Calcul en deux temps : (1) agréger nœuds + `sets` ; (2) marquer `is_bridge` une fois les `sets` des nœuds connus.

## 8. Front — couche visualisation (`web/graph.js`, `web/theme.css`)

**Bibliothèques** (CDN vérifiés au spec global) : `force-graph@1.43.5` (présent), `graphology@0.25.4`, `graphology-library@0.7.0` (louvain + métriques de centralité).

**Analytics (graphology, client)** : à chaque `renderGraph(data)`, construire un `Graph` graphology depuis `{nodes, links}`, calculer (a) **communautés louvain** → `node.community`, (b) **centralité de degré** → `node.centrality`. Recalcul à chaque nouveau graphe (set/transverse) ; pas sur le flux live document (qui garde le rendu incrémental existant via `addFact`).

**Barre de contrôles** (en haut de la zone graphe) :
- **Couleur** : `Communauté` (palette catégorielle dérivée de la charte) · `Set` (une teinte par set_id ; nœud multi-sets → teinte « hub » bordeaux) · `Type` (entité = bleu, valeur = gris) · `Uniforme` (bleu charte, comportement actuel).
- **Taille** : `Centralité` · `Mentions` · `Fixe`.
- **Étiquettes d'arêtes** : toggle on/off (predicate rendu sur l'arête).
- **Chemin** : toggle « chemin le plus long » — **heuristique bornée** (DFS depuis les nœuds de fort degré, fanout plafonné), comme l'original ; pas un plus-long-chemin exact (NP-difficile).
- **Thème** : toggle clair/sombre.

**Arêtes** : largeur/opacité fonction de `confidence` (faible → fine/translucide, élevée → épaisse/opaque) ; `is_bridge` → couleur **bordeaux** prioritaire sur le mode de couleur courant.

**Cartes de fait** : au survol d'une **arête**, popover avec `subject · predicate · object`, `confidence`, set ; texte inséré via `textContent` (jamais `innerHTML` — contenu LLM). Réutilise/échappe comme l'actuel `escapeHtml`.

**Légende** : encart dynamique reflétant le **mode de couleur actif** (communautés listées, ou sets, ou entité/valeur) + rappel « passerelle ».

**Thèmes** : `theme.css` définit deux jeux de variables CSS (clair = charte scriptorium actuelle ; sombre = variante charte). Le toggle bascule un attribut `data-theme` sur `<html>` ; `graph.js` lit les couleurs résolues (background, nœud, arête) pour le canvas force-graph. Persistance du choix en `localStorage`.

**Compat existant** : le flux SSE live (`go` → `addFact` → `redraw`) et la navigation I-4 (sets/docs/recherche/transverse) sont **conservés** ; `renderGraph` (I-4) devient le point d'entrée des analytics. `nodeLabel` garde `label||id`.

## 9. Serving statique (`api.py`)

Ajout de deux routes symétriques à `GET /` :

- `GET /graph.js` → `FileResponse(WEB/"graph.js", media_type="application/javascript")`
- `GET /theme.css` → `FileResponse(WEB/"theme.css", media_type="text/css")`

Pas de montage `StaticFiles` (cohérent avec le `FileResponse` unique existant ; surface minimale).

## 10. Tests

**Backend (TDD, pytest sans réseau)** :
- `store` : `kind` écrit/relu par `create_entity` ; `_GRAPH_COLS` expose `s_kind`/`o_kind`/`set_id` ; `facts_for_entities` joint `documents`.
- `extract` : le parseur lit `subject_kind`/`object_kind` ; défaut `entity` si absent.
- `entities`/`pipeline` : `kind` propagé ; conflit `entity` domine `value`.
- `graph` : `build_graph` agrège `kind` (entity domine), construit `sets` (clés multi-documents), propage `confidence`, marque `is_bridge` (hub multi-sets) ; nœuds mono-set → aucun bridge.
- `api` : `/graph.js` et `/theme.css` répondent 200 avec le bon `content-type` ; les endpoints I-4 renvoient le payload enrichi.

**Front** : non couvert par pytest. Vérification = **suite verte** (régression nulle) + **smoke réel** : re-ingestion (qwen3.6+bge-m3), 4 modes de couleur, taille centralité/mentions, étiquettes d'arêtes, chemin, thèmes clair/sombre, passerelles bordeaux sur un graphe transverse multi-documents, cartes de fait.

## 11. Risques / points de vigilance

- **Qualité du type LLM** : `qwen3.6` doit classer entité/valeur de façon cohérente ; le prompt porte la définition + exemples. Risque résiduel surveillé au smoke ; la politique « entity domine » limite les faux « value ».
- **`is_bridge` vs collapse** : avec le collapse par clé, la passerelle est portée par le **nœud hub** (multi-sets) ; définition simple et calculable, à confirmer visuellement au smoke (le transverse multi-documents doit faire ressortir des arêtes bordeaux autour des entités partagées).
- **graphology sur petits graphes** : louvain peut produire une seule communauté sur un graphe trivial — acceptable (légende à 1 entrée).
- **Canvas + thème** : force-graph peint sur canvas ; au changement de thème il faut repasser `backgroundColor`/`nodeColor`/`linkColor` (re-render), pas seulement basculer le CSS.
- **Taille de `graph.js`** : extraire tôt évite un `index.html` monolithique ; garder `graph.js` lisible (fonctions par responsabilité : analytics, couleur, taille, arêtes, thème).
