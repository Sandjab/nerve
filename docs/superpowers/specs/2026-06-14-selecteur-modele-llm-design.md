# Spec — Sélecteur de modèle LLM d'extraction

**Date** : 2026-06-14
**Branche** : `feat-selecteur-modele-llm` (off `main`, incrément indépendant de la PR #16 « refonte »)
**Statut** : design approuvé, en attente de relecture avant plan d'implémentation

## Contexte & objectif

`nerve` extrait des faits `sujet — relation — objet` en JSON streamé via un LLM
(défaut `qwen3.6`, configurable par `LLM_MODEL`). Le modèle n'est aujourd'hui choisissable
qu'au démarrage, par variable d'environnement. Objectif : **choisir le modèle d'extraction
depuis l'UI**, parmi les modèles réellement disponibles côté provider, avec un **conseil**
sur ceux adaptés à la tâche.

## Décisions verrouillées (brainstorming)

1. **Portée : LLM d'extraction uniquement.** Les embeddings (`bge-m3`) restent intouchés —
   en changer modifierait la dimension des vecteurs (1024) et casserait le magasin `sqlite-vec`
   (recréation DB + ré-embedding). Hors périmètre.
2. **Application : par extraction + `localStorage`.** Le modèle choisi est envoyé dans chaque
   `POST /api/documents` ; absent → repli sur le défaut `LLM_MODEL`. **Backend stateless**
   (pas d'état mutable partagé, pas d'ambiguïté sur la file en cours).
3. **UI : liste des modèles disponibles + tooltip d'aide** (pas de marquage ★). Défaut
   présélectionné.

## Architecture & composants

### 1. Backend — lister les modèles : `GET /api/models`
- Nouvelle fonction dans `nerve/llm.py` : `list_models(cfg: ProviderConfig) -> list[str]`
  qui fait `GET {cfg.base_url}/models` (endpoint **OpenAI-compatible** → marche pour Ollama
  *et* OpenRouter), parse `data[].id`. **Fail-loud** (lève sur statut non-2xx / réseau,
  cohérent avec le client httpx existant).
- Endpoint `nerve/api.py` : `GET /api/models` → renvoie
  `{"models": [...ids...], "default": cfg.llm.model}`, en **excluant** `cfg.embed.model`
  de la liste (éviter de proposer un modèle d'embeddings pour l'extraction).

### 2. Backend — override du modèle par extraction
- `CreateDoc` (Pydantic) gagne `model: str | None = None`.
- Dans `create_document`, si `body.model` est fourni, l'extraction du document utilise
  `dataclasses.replace(cfg.llm, model=body.model)` au lieu de `cfg.llm`. L'override est
  transmis du handler jusqu'à `llm.stream_chat(provider_cfg, …)` via le chemin
  scheduler → pipeline → extract (le câblage exact sera détaillé dans le plan après lecture
  de `scheduler.py` / `pipeline.py`).
- Le `model` retenu est purement par-requête : aucune persistance serveur.

### 3. Front — dropdown (`nerve/web/index.html` + `graph.js` + `theme.css`)
- `<select id="llmModel">` placé dans `#top`, **à côté du bouton « Extraire »** (le choix
  gouverne l'extraction, pas l'affichage du graphe).
- Au chargement : `getJSON('/api/models')` → peuple les `<option>`, présélectionne `default`,
  puis restaure le choix depuis `localStorage` (`nerve-llm-model`) s'il est encore dans la liste.
- À chaque changement : écrit dans `localStorage`.
- Au clic « Extraire » : ajoute `model: <sélection>` au corps du `POST /api/documents`.
- **Tooltip** (`title=`) : « Modèle d'extraction — rapide : qwen2.5:7b-instruct · qualité : qwen3.6 ».

## Flux

```
chargement page → GET /api/models → dropdown peuplée (défaut présélectionné, override localStorage)
utilisateur choisit un modèle → mémorisé (localStorage)
clic « Extraire » → POST /api/documents {title, text, model}
  → extraction streamée avec dataclasses.replace(cfg.llm, model) (ou cfg.llm si absent)
```

## Gestion d'erreur (fail-loud, sans masquer)
- `GET /api/models` échoue (provider injoignable) → l'endpoint **lève** (fail-loud) ; le front
  affiche la bannière d'erreur existante **et** se replie : la dropdown ne contient que le
  défaut configuré, pour que l'extraction reste possible.
- `body.model` inconnu du provider → l'erreur remonte du provider à l'extraction (comportement
  fail-loud déjà en place côté `stream_chat`).

## Tests (hermétiques, sans réseau — TDD)
`tests/` (un fichier par module touché) :
- `list_models` : provider mocké renvoyant `data:[{id},…]` → liste d'ids ; statut non-2xx → lève.
- `GET /api/models` : renvoie `{models, default}`, **embed exclu**.
- `POST /api/documents` avec `model` → l'extraction appelle `stream_chat` avec ce modèle
  (client LLM mocké, assert sur le `model` reçu).
- `POST /api/documents` sans `model` → repli sur `cfg.llm.model` (défaut).

## Hors périmètre
- Sélecteur d'embeddings (risque de dimension).
- Réglage global/persisté côté serveur.
- Téléchargement/`pull` de modèles depuis l'UI.
- Marquage ★ des modèles conseillés (remplacé par le tooltip).

## Workflow
Branche `feat-selecteur-modele-llm` → PR séparée vers `main`, revue `gemini-code-assist[bot]`.
Indépendante de la PR #16 ; rebase si conflit trivial sur `#top`.
