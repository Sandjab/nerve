# nerve

> **N**amed **E**ntity **R**elation **V**isualizer & **E**xtractor — extraction et visualisation
> **locales** de graphes de connaissances.

`nerve` transforme du texte (collé, fichiers PDF/DOCX/HTML/ZIP, ou URLs) en un **graphe de
connaissances** navigable : un LLM extrait des faits `sujet — relation — objet`, les entités
équivalentes sont fusionnées, les faits redondants dédupliqués, et le tout est exploré dans une
page web interactive (force-graph) avec recherche sémantique. Tout tourne **en local** sur votre
machine : aucun service tiers obligatoire, vos données ne quittent pas le poste.

## Fonctionnalités

- **Extraction LLM streamée** : les faits apparaissent en direct (SSE) pendant le traitement.
- **Sources multiples** : texte brut, **URL** (transcodeurs enfichables trafilatura / Pure.md / Jina),
  fichiers **PDF / DOCX / HTML**, archives **ZIP** (traitement résilient du lot).
- **Graphe de qualité** : fusion d'entités équivalentes (garde hybride sémantique + lexicale) et
  déduplication des faits par similarité d'embeddings.
- **Navigation à 3 niveaux** : un **Document**, un **Set** (collection de documents, avec collapse
  des entités partagées en un seul nœud), ou une vue **Transverse** reliant plusieurs sets.
- **Recherche sémantique** sur les faits (vecteurs `sqlite-vec`).
- **Visualisation riche** : communautés (Louvain), taille par centralité, 4 modes de couleur
  (Communauté / Set / Type / Uniforme), confiance → épaisseur des liens, **passerelles** entre sets,
  étiquettes d'arêtes, cartes de fait au survol, thèmes clair / sombre.
- **Reprise** : mise en pause / reprise et reprise après crash, sans ré-extraction des chunks déjà traités.

## Prérequis

- **[`uv`](https://docs.astral.sh/uv/)** (gestion d'environnement et d'exécution Python ≥ 3.11).
- Un **fournisseur LLM + embeddings** exposant une API **compatible OpenAI**. Par défaut, `nerve`
  s'attend à un **[Ollama](https://ollama.com/)** local servant :
  - un modèle d'extraction (défaut `mistral-small3.2:24b-instruct-2506-q8_0`) ;
  - un modèle d'embeddings (défaut `bge-m3`, 1024 dimensions).

  Tirez-les au préalable (p. ex. `ollama pull mistral-small3.2:24b-instruct-2506-q8_0` /
  `ollama pull bge-m3`), ou pointez `nerve`
  vers un autre fournisseur via les variables d'environnement ci-dessous.

> 💡 **Quel modèle d'extraction choisir ?** Un comparatif **mesuré** de plusieurs modèles Ollama
> (fidélité des citations, canonisation des entités, débit) sur Mac Apple Silicon — et la leçon
> « la config de sortie pèse plus que le modèle » — est documenté dans
> **[`Benchmark_LLM.md`](Benchmark_LLM.md)**.

## Démarrage rapide

```bash
# 1. (si besoin) lancer le fournisseur de modèles, p. ex. Ollama
ollama serve

# 2. lancer nerve — uv résout les dépendances et démarre le serveur
uv run nerve
```

Puis ouvrez **http://127.0.0.1:3000**. Collez un texte, cliquez sur **Extraire**, et regardez le
graphe se construire. Pour des graphes riches, ingérez **plusieurs documents** partageant des
entités, répartis sur **plusieurs sets**.

## Configuration

Tout se configure par variables d'environnement (valeurs par défaut entre parenthèses) :

| Variable | Rôle | Défaut |
| --- | --- | --- |
| `LLM_BASE_URL` | endpoint LLM (compatible OpenAI) | `http://localhost:11434/v1` |
| `LLM_API_KEY` | clé du fournisseur LLM | `ollama` |
| `LLM_MODEL` | modèle d'extraction | `mistral-small3.2:24b-instruct-2506-q8_0` |
| `EMBED_BASE_URL` | endpoint embeddings | `http://localhost:11434/v1` |
| `EMBED_API_KEY` | clé du fournisseur embeddings | `ollama` |
| `EMBED_MODEL` | modèle d'embeddings | `bge-m3` |
| `EMBED_DIM` | dimension des vecteurs | `1024` |
| `NERVE_DATA_DIR` | dossier des données (DB + fichiers) | `data` |
| `NERVE_PORT` | port HTTP | `3000` |
| `ENTITY_THRESHOLD` | seuil de fusion d'entités (cosinus) | `0.75` |
| `DEDUP_THRESHOLD` | seuil de déduplication de faits (cosinus) | `0.85` |
| `DEDUP_FIELD` | granularité de dédup | `triple` |
| `URL_TRANSCODERS` | chaîne ordonnée de backends URL→md | `trafilatura` |
| `PUREMD_API_TOKEN` | active le backend Pure.md | _(vide)_ |
| `JINA_API_KEY` | active le backend Jina | _(vide)_ |

> Les fournisseurs LLM et embeddings sont **indépendants** : on peut, par exemple, garder Ollama
> pour les embeddings et basculer l'extraction vers OpenRouter en surchargeant les variables `LLM_*`.

## Manuel illustré

Un guide pas à pas, capture par capture, de chaque fonctionnalité de l'interface :
**[`docs/manuel.html`](docs/manuel.html)** (à ouvrir dans un navigateur).

## Architecture (survol)

Pipeline : `texte/URL/fichier` → `extract` (LLM streamé) → `entities` + `dedup` (fusion/dédup avec
embeddings) → `store` (SQLite + sqlite-vec) → `graph` (`build_graph` pur) → `web/` (force-graph +
graphology). L'orchestration passe par un `scheduler` asyncio (file FIFO + SSE). Détail des
modules et conventions de développement dans **[`CLAUDE.md`](CLAUDE.md)**.

## Développement

```bash
uv run pytest -q   # suite de tests, hermétique (sans réseau)
```

Conventions : **TDD strict**, **fail-loud** (les erreurs remontent), tout via **`uv`**, une branche
par incrément (PR vers `main`). La base de données de développement est **jetable** : à chaque
changement de schéma, `rm -f data/nerve.db*` puis relancer. Voir `CLAUDE.md`.

## Roadmap & limitations connues

Dette technique et évolutions sont suivies dans les **issues GitHub
[#7–#13](https://github.com/Sandjab/nerve/issues)** (index FK, re-calibration des seuils,
type catégoriel, bornes d'analyse de graphe, etc.).

## Licence

[MIT](LICENSE).
