# nerve — Plan 2 / I-2 · Ingestion (URL / fichiers / zip)

**Date** : 2026-06-13
**Statut** : design validé en brainstorming, en attente de relecture avant plan d'implémentation
**Incrément** : I-2 (dépend du Plan 1 uniquement). Branche : `nerve-i2-ingestion`.
**Sources de cadrage** : spec globale §2.1 (transcodeurs), §4 (modèle de données), §7 (API), §8 (risques) ; HANDOFF Plan 2 §3.

## 1. But

Permettre de nourrir nerve au-delà du texte collé : **URL** (transcodage enfichable vers
markdown) et **fichiers / zip** (pdf, docx, html, texte, code…). Un **zip = un document** dont
chaque fait porte sa provenance (`source_file`), conformément à la spec globale §1/§4.

Aujourd'hui l'API n'accepte que du texte (`POST /api/documents` avec un champ `text`). I-2 ajoute
les chemins URL et fichier/zip, et branche `source_file` (déjà présent dans le schéma `facts` et la
signature `store.add_fact`, mais jamais rempli).

## 2. Périmètre

**Dans I-2** :
- `transcode.py` — transcodeurs URL → `(markdown, title)` enfichables + chaîne de repli.
- `ingest.py` — lecture de fichiers (pdf/docx/html/texte) + parcours zip sûr.
- `POST /api/documents/upload` (multipart) + champ `url` sur `POST /api/documents`.
- `config.py` — chaîne de transcodeurs + clés distantes.
- Adaptation de `run_extraction` aux **segments** `(text, source_file)`.
- Dépendance `python-docx`.
- Correctif **M-2** au passage (dedup_field non persisté — HANDOFF §6).

**Hors I-2** (inchangé, traité ailleurs) :
- SSE live, scheduler, pause/reprise → **I-3**. L'upload **consomme `run_extraction` jusqu'au
  bout** (synchrone), exactement comme l'actuel `POST /api/documents`.
- Recherche / transverse → I-4.
- Éclatement d'un zip en *set de documents* (un document par fichier) : différé (spec §1).
- Fusion/dedup cross-document : hors v1 (spec §9).

## 3. Décisions arrêtées (brainstorming)

- **Politique d'erreur (résilience de lot)** : dans un **zip**, un fichier illisible/vide est
  **ignoré et consigné** (`skipped`), le document continue. Un **upload mono-fichier** illisible,
  ou un **zip dont 100 % des fichiers supportés sont illisibles**, déclenche un **échec remonté**
  (fail-loud). Principe : fail-loud sur l'unité que l'utilisateur a explicitement choisie,
  tolérant sur le lot. C'est une nuance assumée au principe « fail-loud » du projet, motivée par
  l'UX d'un gros corpus zip.
- **Lecture des formats** : ajout de la dépendance **`python-docx`** (docx robuste : paragraphes +
  tableaux), plutôt que le parseur regex maison de l'original.
- **Multi-fichiers → pipeline** : approche **segments partagés** (cf. §7) — `resolver` et `deduper`
  uniques pour tout le document, afin que la fusion d'entités et la dedup (cœur d'I-1) traversent
  tous les fichiers d'un zip. Alternatives écartées : concaténation (perte de `source_file`, méga-
  doc mélangé) ; N appels `run_extraction` (resolver/deduper non partagés → régression dedup).

## 4. `transcode.py` — URL → (markdown, title)

Mécanisme générique : une **chaîne ordonnée de backends** partageant l'interface
`transcode(url, *, client) -> tuple[str, str] | None` (markdown, titre). `None` ou exception = échec
→ repli sur le backend suivant.

Backends (spec §2.1) :

| Backend | Type | Activation | Note |
|---------|------|------------|------|
| `trafilatura` | local | toujours (défaut) | `fetch_url` + `extract(output_format="markdown")` ; titre via les métadonnées trafilatura |
| `puremd` | distant | `PUREMD_API_TOKEN` défini | Pure.md — **endpoint exact à confirmer** (motif présumé `https://pure.md/<url>` + en-tête `x-puremd-api-token`, spec §8) |
| `jina` | distant | `JINA_API_KEY` défini | `GET https://r.jina.ai/{url}`, `Authorization: Bearer <clé>`, `Accept: text/markdown` ; titre = 1re ligne `Title:` ou `# …` (porté de l'original `app.py` l.249-267) |

Orchestrateur :
```
async def transcode_url(cfg, url, *, client) -> tuple[str, str]:
    parcourt cfg.url_transcoders dans l'ordre
    retient le 1er résultat dont le markdown est non vide (après strip)
    repli silencieux sur échec/vide
    si TOUS échouent -> RuntimeError  # fail-loud
```

Les backends distants envoient l'URL/le contenu à un tiers (note de confidentialité — déjà dans la
spec globale). API exacte de `trafilatura` (extraction + titre) et endpoint `puremd` : **à vérifier
via context7 / doc au moment du plan d'implémentation** (le plan TDD portera le code exact).

## 5. `ingest.py` — fichiers & zip

### Lecture d'un fichier
```
read_file(path, name) -> str
```
Dispatch par extension :
- `.pdf` → `pypdf.PdfReader`, concatène `page.extract_text()`.
- `.docx` → `python-docx` : texte des paragraphes + cellules de tableaux.
- `.html` / `.htm` → `trafilatura.extract` (markdown) sur le contenu lu localement (plus propre que
  le regex de l'original, et trafilatura est déjà une dépendance).
- défaut (`.txt .md .markdown .rst .json .jsonl .csv .tsv .log .xml .yaml .yml .py .js .ts …`) →
  lecture utf-8 (`errors="ignore"`).

**Lève `IngestError` si le contenu extrait est vide ou si la lecture échoue** (différence assumée
avec l'original qui renvoyait `""` silencieusement → fail-loud).

`TEXT_EXTS` (ensemble des extensions supportées) porté de l'original `app.py` l.302-304.

### Ingestion d'un upload
```
ingest_upload(filename, raw: bytes, dest_dir) -> tuple[list[Segment], list[str]]
    # Segment = (text, source_file) ; retourne (segments, skipped)
```
- **Conserve l'input brut** sous `dest_dir` (= `data/inputs/<doc_id>/`, spec §4).
- Si `.zip` :
  - extraction **sûre** sous `dest_dir` : ignore dossiers, fichiers cachés, `__MACOSX`, extensions
    non supportées ; **anti path-traversal** (`os.path.abspath(target)` doit rester sous
    `dest_dir`) — porté/corrigé de l'original l.307-330.
  - pour chaque fichier extrait : `try: read_file(...) ; except IngestError: skipped.append(name)`.
  - tri par nom ; `source_file = nom relatif dans le zip`.
  - **si `segments` est vide alors que le zip contenait des fichiers supportés → `IngestError`**
    (zip 100 % illisible = fail-loud).
- Sinon (fichier simple) : écrit le brut, `read_file` direct (l'`IngestError` éventuelle remonte),
  `segments = [(text, "")]`, `skipped = []`.

## 6. `config.py`

Ajouts à `Config` / `load_config` :
- `url_transcoders: tuple[str, ...]` — env `URL_TRANSCODERS` (CSV), défaut `("trafilatura",)`.
- `puremd_token: str` — env `PUREMD_API_TOKEN`, défaut `""`.
- `jina_key: str` — env `JINA_API_KEY`, défaut `""`.

Un backend distant n'est tenté que si sa clé est non vide **et** qu'il figure dans
`url_transcoders` (la présence dans la chaîne pilote l'ordre ; la clé pilote l'activation effective).

## 7. `pipeline.py` — segments partagés

Signature :
```
run_extraction(cfg, store, doc_id, segments, *, client=None)
    # segments: list[tuple[str, str]]  (text, source_file)
```
- `resolver = EntityResolver(...)` et `deduper = FactDeduper(...)` créés **une seule fois**, avant
  la boucle (inchangé pour le mono-segment).
- Boucle externe sur `segments`, boucle interne sur `chunk_text(text)` (existante).
- `store.add_fact(doc_id, fact, …, source_file=sf)`.
- Events `fact` enrichis de `"source_file": sf`. Reste des events (`round_end`, `done`, `error`)
  inchangé. Gestion d'erreur fail-loud inchangée (`finish_document(error=…)` + `raise`).

Le mono-segment (`[(text, "")]`) reproduit exactement le comportement actuel.

## 8. `api.py`

### `POST /api/documents` (text | url)
`CreateDoc` gagne `url: str | None = None`.
- `url` fourni → `md, title = await transcode_url(cfg, url, client=…)` ; `segments=[(md, "")]` ;
  `source_kind="url"` ; `source_ref=url`. Titre du document : `body.title` s'il a été personnalisé
  (≠ défaut `"Sans titre"`) ; sinon le `title` transcodé s'il est non vide ; sinon l'URL.
- sinon `text` → `segments=[(text, "")]` ; `source_kind="text"`.
- `create_document(..., params={"dedup_field": cfg.dedup_field})` → **règle M-2**.

### `POST /api/documents/upload` (multipart)
- Form : `file: UploadFile`, `set_id: int | None`, `set_name: str = "Défaut"`, `title: str = ""`.
- `set_id = body.set_id or store.create_set(set_name)`.
- `doc_id = create_document(set_id, title|filename, "file", source_ref=filename,
  params={"dedup_field": cfg.dedup_field})`.
- `dest = data/inputs/<doc_id>/` ; `segments, skipped = ingest_upload(filename, raw, dest)`.
- `run_extraction(cfg, store, doc_id, segments)` consommé jusqu'au bout.
- Réponse : `{document_id, total_facts, unique_facts, duplicate_facts, status, skipped}`.

Ordre `create_document → ingest_upload` : nécessaire pour que `dest_dir` connaisse `doc_id`.
Si `ingest_upload` lève (mono-fichier ou zip 100 % illisible) → on marque le document `failed`
(`finish_document(doc_id, error=…)`) puis on remonte une `HTTPException 422`.

## 9. Tests (sans réseau)

- **`test_config`** : `URL_TRANSCODERS="trafilatura,jina"` → tuple ordonné ; défaut `("trafilatura",)`.
- **`test_transcode`** : chaîne avec backends factices (1er vide → repli sur le 2e) ; parse du
  `Title:` jina ; **fail-loud si tous échouent** ; un backend distant sans clé est sauté. `httpx`
  mocké (pas de réseau).
- **`test_ingest`** :
  - zip en `tmp_path` mêlant `.txt`, un `.pdf` minimal réel et un fichier corrompu → le corrompu
    est dans `skipped`, les autres en `segments` avec `source_file` = nom dans le zip ;
  - tentative de path-traversal (`../evil`) ignorée ;
  - `.docx` réel minimal lu via python-docx ;
  - mono-fichier illisible → `IngestError` ; zip 100 % illisible → `IngestError`.
- **`test_pipeline`** : signature `segments` — multi-segments → `source_file` correct par fait ;
  un doublon réparti **sur deux segments** est bien dédupliqué (dedup partagée cross-fichier).
- **`test_api`** : `/upload` avec zip mocké → réponse expose `skipped` ; `url` sur `/documents`
  → `transcode_url` mocké, `source_kind="url"`, `source_ref` rempli ; `params_json` contient
  `dedup_field` (M-2). `run_extraction`/`transcode` mockés.

## 10. Dépendances

`pyproject.toml` : ajout de `python-docx>=1.1`. (`pypdf`, `trafilatura`, `python-multipart` déjà
présents ; `zipfile` est stdlib.)

## 11. Vérifications réelles (smoke, hors tests automatiques)

Après implémentation, piloté par le contrôleur (Ollama requis) :
- une **URL** réelle via trafilatura → faits extraits ;
- un **zip** réel multi-fichiers → `source_file` rempli par fait, dedup traverse les fichiers ;
- un **docx** réel.
- Rappel HANDOFF : supprimer `data/nerve.db*` avant smoke si le schéma a bougé (ici le schéma ne
  change pas — `source_file` existe déjà — mais `data/inputs/` est nouveau).
