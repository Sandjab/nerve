# Benchmark LLM — modèles d'extraction pour Nerve

**Date** : 2026-06-14 · **Machine** : Mac Studio M3 Ultra, 96 Go de mémoire unifiée
(28 cœurs : 20 P + 8 E ; ~819 Go/s de bande passante) · **Runtime** : Ollama (llama.cpp + Metal).

Objectif : déterminer, parmi les modèles compatibles Ollama tournant sur ce Mac, lesquels
sont les plus adaptés à **l'extraction de graphe de connaissances de Nerve** — c.-à-d. produire
un tableau JSON de faits atomiques (`subject`/`predicate`/`object` + `kind` + `evidence_span` +
`description`/`tags`/`confidence`), en français, de façon **fiable et fidèle**. Ce n'est **pas**
un benchmark de chat : on mesure le respect du contrat d'extraction, pas l'éloquence.

> **Résultat principal — la config pèse plus que le modèle.** Le facteur décisif de fiabilité
> est le **schéma JSON de sortie + la température**, pas le LLM. À la config actuelle de Nerve
> (schéma `strict=False`, `temperature=0.7`), *tous* les modèles laissent tomber des champs au
> hasard d'un run à l'autre. En **schéma strict + temp 0.2**, *tous* atteignent **100 % de
> complétude** — y compris gemma4-8B.

---

## 1. Modèles évalués

Parc déjà installé + un modèle tiré pour ce banc (`mistral-small3.2`, profil non-thinking FR manquant).

| Modèle | Taille | Architecture | Quant | Ctx max | Thinking | Défauts sampling notables |
|---|---|---|---|---|---|---|
| `qwen3.6` *(défaut Nerve)* | 23 Go | qwen35moe, 36 B MoE | Q4_K_M | 262 144 | oui | **`presence_penalty 1.5`**, temp 1, top_k 20, top_p 0.95 |
| `gpt-oss:120b` | 65 Go | gptoss, 116,8 B MoE | MXFP4 | 131 072 | oui | `Reasoning: medium` par défaut, temp 1 |
| `mistral-small3.2:24b-instruct-2506-q8_0` *(tiré)* | 26 Go | mistral, 24 B dense | Q8_0 | 131 072 | **non** | temp 0.15, **aucune pénalité** |
| `gemma4` | 9,6 Go | gemma4, 8 B | Q4_K_M | 131 072 | oui | temp 1, top_k 64, top_p 0.95 |

Embeddings : **bge-m3** (1024 dims) — inchangé, voir §7.

> ⚠️ `qwen3.6`, `gemma4` et le format `qwen35moe`/`gemma4` sont postérieurs à la coupe de
> connaissances de l'assistant : les specs ci-dessus viennent de `ollama show`, pas de mémoire.

---

## 2. Méthodologie

Banc : [`scripts/bench_models.py`](scripts/bench_models.py). Il **réutilise les modules réels de
Nerve** pour coller au comportement de production :

- prompt et parseur : `nerve.extract` (`SYSTEM_PROMPT`, `build_messages`, `FactStreamParser`, `FACT_SCHEMA`) ;
- appel LLM streamé : `nerve.llm.stream_chat` (mêmes conditions que `nerve/pipeline.py` : `response_format`, streaming) ;
- canonisation : `nerve.embeddings.embed` (bge-m3) + `nerve.entities.normalized_key`/`lexical_guard` (la **garde hybride** réelle de Nerve).

**Document source** : prose française dense du domaine (Cluny / réforme grégorienne), ~2,4 k
caractères → tient dans **un seul chunk** (limite Nerve = 24 000 car.) et **sous le contexte chargé**
(pas de troncature). Les entités apparaissent sous plusieurs formes de surface
(*Cluny* / *l'abbaye de Cluny* / *l'abbaye bourguignonne* ; *Grégoire VII* / *Hildebrand*) pour
tester réellement la canonisation.

**Métriques** (toutes calculées par code, pas par jugement LLM) :

| Métrique | Définition | Pourquoi pour Nerve |
|---|---|---|
| **span✓** (fidélité `evidence_span`) | % de faits dont `evidence_span` (espaces normalisés) est une **sous-chaîne réelle** du document | Un modèle qui paraphrase casse la citation/preuve |
| **complet** | % de faits avec **tous** les champs du contrat non vides | Champs manquants = faits dégradés |
| **kinds** | % de faits portant `subject_kind` **et** `object_kind` | Pilote la vue Type (entité vs valeur) ; le prompt l'exige |
| **pred** | % de `predicate` en snake_case (accents FR tolérés) ≤ 32 car. | Cohérence des arêtes |
| **frag** | nb de formes de surface d'entité que Nerve **doit fusionner** (variantes évitables) | Plus bas = meilleure canonisation des nœuds |
| **faits / temps** | nb de faits extraits / temps mural | Rappel et débit (la file FIFO mono-worker enchaîne les chunks) |

---

## 3. Résultat principal : la config pèse plus que le modèle

Même modèle (`gemma4` 8 B), deux configurations :

| Config | span✓ | complet | kinds | pred |
|---|---|---|---|---|
| **Config Nerve actuelle** (schéma `strict=False`, temp 0.7) | 64–77 % | **0 %** | variable | 38–45 % |
| **Schéma strict** (tous champs requis) **+ temp 0.2** | **100 %** | **100 %** | **100 %** | **100 %** |

Le schéma strict transforme un 8 B en extracteur parfaitement conforme. Le même effet s'observe
sur tous les modèles (§5).

### Instabilité à la config actuelle (preuve)

À schéma lâche + temp 0.7, le **même** appel donne des résultats contradictoires d'un run à l'autre
— les champs `kind`/`evidence_span` tombent aléatoirement :

| Run (`qwen3.6 /no_think`, lâche/0.7) | span✓ | complet | kinds |
|---|---|---|---|
| run A | 100 % | 100 % | 100 % |
| run B | 75 % | **0 %** | **0 %** *(kinds absents du JSON)* |

| Run (`mistral q8`, lâche/0.7) | faits | span✓ | complet |
|---|---|---|---|
| run A | 23 | **0 %** *(aucun `evidence_span`)* | 0 % |
| run B | 18 | 100 % | 0 % *(kinds absents)* |

Cause : le `response_format` de Nerve déclare `strict=False` avec **seulement** `subject`/`predicate`/`object`
requis. Les modèles arbitrent alors librement entre « suivre le prompt » (tous les champs) et
« suivre le schéma » (le minimum) — non déterministe à température 0.7.

---

## 4. Détail — config Nerve actuelle (schéma lâche, temp 0.7, 1 run/config)

Chiffres **bruités** (1 run) : à lire comme illustration de l'instabilité, pas comme classement.

| Config | faits | temps | span✓ | complet | kinds | pred |
|---|---|---|---|---|---|---|
| `qwen3.6` (thinking — **défaut Nerve**) | 10 | 114 s | — | 0 % | 100 % | 50 % |
| `qwen3.6` (`/no_think`) | 11 | 90 s | 100 % | 100 % | 100 % | 100 % |
| `gpt-oss:120b` (reasoning défaut) | 16 | 88 s | 75 % | 100 % | 100 % | 100 % |
| `gpt-oss:120b` (reasoning=low) | 15 | 90 s | 100 % | 100 % | 100 % | 100 % |
| `mistral q8` (non-thinking) | 23 | 110 s | — | 0 % | 100 % | 87 % |
| `gemma4` 8 B | 13 | 33 s | 77 % | 0 % | 100 % | 38 % |

`—` = aucun `evidence_span` émis sur ce run.

---

## 5. Comparatif en bonne config (schéma strict + temp 0.2, **3 runs/modèle**)

Pooled sur les 3 runs. **Tous à 100 % de complétude et de kinds** — le schéma strict a éliminé l'instabilité.

| Modèle | faits (min–max) | temps moy. | span✓ | complet | kinds | pred | frag |
|---|---|---|---|---|---|---|---|
| **`mistral-small3.2 q8`** | 12–15 | 87 s | 92 % | 100 % | 100 % | 95 % | **0** |
| **`gpt-oss:120b`** *(reasoning=low)* | **13–16** | **42 s** | 86 % | 100 % | 100 % | 88 % | 1 |
| **`qwen3.6`** *(/no_think)* | 10 | 95 s | **97 %** | 100 % | 100 % | 100 % | 1 |
| `gemma4` 8 B *(réf., 1 run)* | 10 | 42 s | 100 % | 100 % | 100 % | 100 % | 0 |

Lecture par priorité :

- **Fidélité `evidence_span`** : `qwen3.6` (97 %) > `mistral` (92 %) > `gpt-oss` (86 %).
- **Rappel (nb de faits)** : `gpt-oss` (13–16) ≈ `mistral` (12–15) > `qwen3.6` (10).
- **Débit** : `gpt-oss` (42 s) ≫ `mistral` (87 s) ≈ `qwen3.6` (95 s).
- **Canonisation** : `mistral` (frag 0 sur les 3 runs) > les autres (frag 1).

> **Surprise robuste** (cohérente sur 3 runs) : `gpt-oss:120b` (116 B) est **~2,5× plus rapide** que
> `qwen3.6` (36 B) sur ce M3 Ultra — MXFP4 + ~5 B de paramètres actifs vs MoE Q4_K_M plus lourd.

---

## 6. Observations Ollama

- **Contexte chargé = 32 768 tokens** (`ollama ps`) pour tous les modèles testés → les chunks Nerve
  (≤ 24 000 car. ≈ 7–8 k tokens) **passent sans troncature**. **Aucun Modelfile `num_ctx` nécessaire.**
- **`qwen3.6` impose `presence_penalty 1.5`** dans son Modelfile. Nerve n'override **que la
  température** → cette pénalité est **active en production**. Pénaliser la répétition est
  contre-productif ici : Nerve a besoin de **répéter à l'identique** les noms d'entités (fusion des
  nœuds) et de **citer verbatim** (`evidence_span`). C'est une raison de plus de quitter ce défaut.
- **`mistral-small3.2`** a des défauts **idéaux** pour l'extraction (temp 0.15, aucune pénalité) et
  est **non-thinking** → il tourne en mode optimal sous Nerve **sans aucun réglage**.
- **`gpt-oss:120b`** raisonne en **`medium` par défaut** : sous Nerve (qui n'envoie pas de contrôle
  de raisonnement), il est en mode lent. Le `reasoning_effort: low` (rapide, ci-dessus) **n'est pas
  atteignable depuis Nerve sans changement de code**.
- **Désactiver le thinking via Modelfile : non fiable.** Un `gpt-oss-nerve` avec
  `SYSTEM "Reasoning: low"` a été testé : temps à chaud 49–67 s (entre `low` 30–36 s et `medium`
  87 s) et fidélité qui décroche (67–87 %) → la consigne est partiellement écrasée par le system
  message de Nerve. **Artefact supprimé.** Conclusion : aucun Modelfile ne pilote proprement le
  raisonnement de ces modèles sous Nerve ; il faut soit un modèle nativement non-thinking, soit une
  modif côté Nerve.

---

## 7. Recommandations

### Modèle

1. **Défaut conseillé — `mistral-small3.2:24b-instruct-2506-q8_0`** : le plus adapté à Nerve **tel
   quel** (non-thinking, défauts d'extraction idéaux, meilleure canonisation `frag 0`, fidélité
   92 %, français natif). Aucune modif requise pour qu'il tourne en mode optimal.
   ```bash
   LLM_MODEL=mistral-small3.2:24b-instruct-2506-q8_0 uv run nerve
   ```
2. **Option débit/rappel — `gpt-oss:120b`** : le plus rapide et le plus exhaustif, **à condition**
   d'envoyer `reasoning_effort: low` (donc une modif Nerve). Fidélité verbatim un cran en dessous.
3. **Option fidélité — `qwen3.6` `/no_think`** : meilleure fidélité `evidence_span`, mais le plus
   lent et le plus faible en rappel ; nécessite de couper le thinking (modif Nerve) et de neutraliser
   `presence_penalty`.

### Embeddings — **ne pas changer**

Garder **bge-m3**. Les seuils `ENTITY_THRESHOLD=0.75` et `DEDUP_THRESHOLD=0.85` sont calibrés sur sa
distribution de cosinus ; changer d'embedder imposerait de **re-calibrer** (cf. `scripts/calibrate_thresholds.py`).

### Code Nerve — le gain le plus rentable (à faire en PR)

Indépendant du modèle, c'est ce qui rend l'extraction **fiable** :

- `nerve/extract.py` — `FACT_RESPONSE_FORMAT` → **strict, tous les champs requis**.
- `nerve/pipeline.py` — `temperature=0.7` → **0.2** (l'extraction veut du déterminisme/fidélité).
- *(optionnel)* exposer un contrôle de raisonnement (`/no_think` Qwen, `reasoning_effort` gpt-oss)
  pour récupérer le débit sur les modèles thinking.

---

## 8. Reproduire

```bash
# config recommandée (schéma strict + temp basse), 3 runs/modèle
uv run python scripts/bench_models.py \
  --models qwen3.6 gpt-oss:120b mistral-small3.2:24b-instruct-2506-q8_0 \
  --strict --temp 0.2 --repeat 3

# config Nerve actuelle (schéma lâche, temp 0.7) pour comparaison
uv run python scripts/bench_models.py --temp 0.7 --repeat 3

# laisser le raisonnement actif (mesurer la taxe thinking)
uv run python scripts/bench_models.py --models qwen3.6 --thinking --repeat 3
```

---

## 9. Limites du banc (à garder en tête)

- **Un seul document**, un seul domaine (médiéval FR), un seul chunk. Les écarts entre modèles
  peuvent bouger sur des textes techniques, multilingues ou très longs.
- **n = 3 runs** : réduit le bruit mais ne l'élimine pas (cf. les écarts de span 77–100 % intra-modèle).
- **`span✓`** ne normalise que les espaces : une différence de ponctuation entre la citation et le
  texte compte comme un échec, donc la fidélité réelle est ≥ la valeur affichée.
- **`pred`** mesure un snake_case tolérant aux accents ; il ne juge pas la *pertinence* du prédicat.
- Métriques **structurelles**, pas sémantiques : le banc ne mesure pas si les faits extraits sont
  *justes* (vérité historique), seulement s'ils respectent le contrat et citent fidèlement la source.
