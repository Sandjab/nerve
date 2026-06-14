"""Banc de comparaison de modèles LLM pour l'extraction Nerve.

Mesure objectivement, sur un document FR dense du domaine (Cluny / réforme
grégorienne), ce qui compte VRAIMENT pour Nerve — pas un benchmark de chat :

  1. Fidélité de evidence_span : % de faits dont la citation est une vraie
     sous-chaîne du document (un modèle qui paraphrase casse ce champ).
  2. Canonisation des entités : nombre de formes de surface que Nerve doit
     fusionner (= incohérence évitable ; le prompt exige des chaînes IDENTIQUES).
  3. Respect du contrat du prompt : champs complets, subject_kind/object_kind
     jamais omis, predicate snake_case <=32 car., confidence 0-100.
  4. Débit : temps mural + faits/s (la file FIFO mono-worker enchaîne les chunks).

LEÇON MESURÉE : à temperature=0.7 + schéma lâche (response_format strict=False,
seuls subject/predicate/object requis), la complétude des champs est NON
DÉTERMINISTE d'un run à l'autre. D'où --repeat (moyenne + variance) et les leviers
--temp / --strict (schéma exigeant tous les champs) pour stabiliser.

Le banc réutilise les modules RÉELS de Nerve (extract / llm / embeddings /
entities) et appelle le LLM comme pipeline.py (response_format, chunk unique).

Usage :
    uv run python scripts/bench_models.py --temp 0.2 --strict --repeat 3
    uv run python scripts/bench_models.py --models qwen3.6 --thinking --repeat 3
Prérequis : Ollama servant les modèles + bge-m3 (EMBED_MODEL).
"""
import argparse
import asyncio
import dataclasses
import re
import time

import httpx

from nerve.config import load_config
from nerve.extract import (build_messages, FactStreamParser, FACT_SCHEMA,
                           FACT_RESPONSE_FORMAT)
from nerve.llm import stream_chat
from nerve.embeddings import embed
from nerve.entities import normalized_key, lexical_guard

SOURCE_DOC = """\
L'abbaye de Cluny est fondée en 910 par Guillaume Ier d'Aquitaine, duc d'Aquitaine \
surnommé Guillaume le Pieux. Par sa charte de fondation, le duc place le monastère \
sous la protection directe du Saint-Siège, le soustrayant à toute tutelle épiscopale \
ou seigneuriale. Cette indépendance fait la force de l'abbaye bourguignonne. Le \
premier abbé, Bernon de Baume, installe la communauté en Bourgogne et lui impose \
l'observance stricte de la règle de saint Benoît.

À la mort de Bernon en 927, Odon de Cluny lui succède. Odon étend le rayonnement \
spirituel de Cluny bien au-delà de la Bourgogne et réforme de nombreux monastères \
selon le modèle clunisien. Sous son abbatiat, Cluny devient la tête d'un réseau de \
prieurés qui formera l'ordre clunisien, lequel comptera à son apogée près de mille \
maisons à travers l'Europe.

L'apogée de Cluny est atteint sous Hugues de Semur, élu abbé en 1049. Son très long \
gouvernement, d'environ soixante ans jusqu'à sa mort en 1109, coïncide avec la \
construction de la grande église abbatiale, la plus vaste de la chrétienté latine. \
Hugues conseille les papes et les souverains.

C'est dans cet esprit clunisien que se forme Hildebrand, futur pape Grégoire VII. \
Élu en 1073, Grégoire VII engage une réforme énergique, connue sous le nom de réforme \
grégorienne. Il combat la simonie, c'est-à-dire le commerce des charges \
ecclésiastiques, et interdit aux laïcs d'investir les évêques. Ce refus de \
l'investiture laïque déclenche un affrontement durable avec l'empereur Henri IV : \
c'est la querelle des Investitures.

Excommunié par Grégoire VII, Henri IV doit céder. À l'hiver 1077, l'empereur traverse \
les Alpes et vient implorer le pardon du pape au château de Canossa, où il demeure \
trois jours en pénitent dans la neige. L'humiliation de Canossa devient le symbole de \
la victoire momentanée de la papauté sur l'Empire.
"""

# Schéma LÂCHE : seuls subject/predicate/object requis (ancien défaut de Nerve avant le
# passage en strict). Sert de point de comparaison face à FACT_RESPONSE_FORMAT (désormais
# strict, tous champs requis) pour mesurer l'apport du mode strict.
LOOSE_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "facts", "strict": False,
                    "schema": {"type": "array", "items": FACT_SCHEMA}},
}

# Spécificités par modèle : interrupteur de raisonnement.
MODEL_SPECS = {
    "qwen3.6": {"no_think": True},                  # /no_think par défaut (thinking via --thinking)
    "gpt-oss:120b": {"params": {"reasoning_effort": "low"}},
    "mistral-small3.2:24b-instruct-2506-q8_0": {},
    "gemma4": {},
}

REQUIRED_FIELDS = ["title", "description", "subject", "predicate", "object",
                   "subject_kind", "object_kind", "evidence_span", "confidence", "tags"]
# snake_case tolérant aux accents FR (premier_abbé est valide) ; <=32 contrôlé à part.
PRED_RE = re.compile(r"^[a-z0-9à-öø-ÿ]+(_[a-z0-9à-öø-ÿ]+)*$")


def _kind(raw) -> str:
    return "value" if str(raw or "").strip().lower() == "value" else "entity"


def _norm_ws(s: str) -> str:
    return " ".join((s or "").split())


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


async def run_one(cfg, model, spec, temp, response_format, thinking, client):
    llm = dataclasses.replace(cfg.llm, model=model)
    msgs = build_messages(SOURCE_DOC)
    if spec.get("no_think") and not thinking:
        msgs[0] = {**msgs[0], "content": msgs[0]["content"] + "\n\n/no_think"}
    params = dict(spec.get("params", {}))
    if thinking:
        params.pop("reasoning_effort", None)
    parser = FactStreamParser()
    facts, raw = [], []
    t0 = time.perf_counter()
    async for delta in stream_chat(llm, msgs, client=client,
                                   response_format=response_format,
                                   temperature=temp, **params):
        raw.append(delta)
        facts.extend(parser.feed(delta))
    return {"facts": facts, "raw": "".join(raw), "elapsed": time.perf_counter() - t0}


async def evaluate(cfg, res, client):
    facts, elapsed = res["facts"], res["elapsed"]
    doc_n = _norm_ws(SOURCE_DOC)
    n = len(facts)
    spans = [f.get("evidence_span") for f in facts if f.get("evidence_span")]
    faithful = sum(1 for s in spans if _norm_ws(s) in doc_n)
    complete = sum(1 for f in facts if all(
        f.get(k) not in (None, "", []) or k == "confidence" for k in REQUIRED_FIELDS)
        and f.get("confidence") is not None)
    has_kinds = sum(1 for f in facts if f.get("subject_kind") and f.get("object_kind"))
    pred_ok = sum(1 for f in facts if isinstance(f.get("predicate"), str)
                  and PRED_RE.match(f["predicate"]) and len(f["predicate"]) <= 32)

    surface_set: set[str] = set()
    for f in facts:
        if f.get("subject") and _kind(f.get("subject_kind")) == "entity":
            surface_set.add(f["subject"].strip())
        if f.get("object") and _kind(f.get("object_kind")) == "entity":
            surface_set.add(f["object"].strip())
    surfaces = sorted(s for s in surface_set if s)
    fragmentation = 0
    if surfaces:
        vecs = await embed(cfg.embed, surfaces, client=client)
        keys = [normalized_key(s) for s in surfaces]
        parent = list(range(len(surfaces)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in range(len(surfaces)):
            for j in range(i + 1, len(surfaces)):
                if keys[i] == keys[j] or (lexical_guard(keys[i], keys[j])
                                          and _dot(vecs[i], vecs[j]) >= cfg.entity_threshold):
                    parent[find(i)] = find(j)
        n_groups = len({find(i) for i in range(len(surfaces))})
        fragmentation = len(surfaces) - n_groups
    return {"n": n, "elapsed": elapsed, "span_cov": len(spans), "span_faithful": faithful,
            "complete": complete, "has_kinds": has_kinds, "pred_ok": pred_ok,
            "distinct_ent": len(surfaces), "fragmentation": fragmentation}


def _pct(num, den):
    return f"{100*num/den:.0f}%" if den else "—"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", default=list(MODEL_SPECS))
    ap.add_argument("--temp", type=float, default=0.2)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--strict", action="store_true", help="schéma exigeant tous les champs")
    ap.add_argument("--thinking", action="store_true", help="laisser le raisonnement actif")
    args = ap.parse_args()
    cfg = load_config()
    rf = FACT_RESPONSE_FORMAT if args.strict else LOOSE_RESPONSE_FORMAT
    print(f"config : temp={args.temp}  schéma={'STRICT' if args.strict else 'lâche'}  "
          f"thinking={'ON' if args.thinking else 'OFF'}  repeat={args.repeat}")

    agg = []
    async with httpx.AsyncClient(timeout=None) as client:
        for model in args.models:
            spec = MODEL_SPECS.get(model, {})
            runs = []
            print(f"\n▶ {model}")
            for r in range(args.repeat):
                try:
                    res = await run_one(cfg, model, spec, args.temp, rf, args.thinking, client)
                    ev = await evaluate(cfg, res, client)
                except Exception as e:
                    print(f"  run {r+1}: ✗ {type(e).__name__}: {e}")
                    continue
                runs.append(ev)
                print(f"  run {r+1}: {ev['n']:>2} faits  {ev['elapsed']:>5.0f}s  "
                      f"span {_pct(ev['span_faithful'], ev['span_cov']):>4}  "
                      f"complet {_pct(ev['complete'], ev['n']):>4}  "
                      f"kinds {_pct(ev['has_kinds'], ev['n']):>4}  "
                      f"pred {_pct(ev['pred_ok'], ev['n']):>4}  frag {ev['fragmentation']}")
            if runs:
                agg.append((model, runs))

    # Récap agrégé (pooled sur les runs)
    print("\n" + "=" * 104)
    print(f"RÉCAP  (temp={args.temp}, schéma={'STRICT' if args.strict else 'lâche'}, "
          f"thinking={'ON' if args.thinking else 'OFF'}, n={args.repeat} runs/modèle)".center(104))
    print("=" * 104)
    print(f"{'modèle':<44}{'faits~':>8}{'temps~':>8}{'span':>7}{'complet':>9}{'kinds':>7}{'pred':>7}{'frag':>6}")
    print("-" * 104)
    for model, runs in agg:
        rn = len(runs)
        f_lo, f_hi = min(r["n"] for r in runs), max(r["n"] for r in runs)
        t_mean = sum(r["elapsed"] for r in runs) / rn
        span = _pct(sum(r["span_faithful"] for r in runs), sum(r["span_cov"] for r in runs))
        comp = _pct(sum(r["complete"] for r in runs), sum(r["n"] for r in runs))
        kinds = _pct(sum(r["has_kinds"] for r in runs), sum(r["n"] for r in runs))
        pred = _pct(sum(r["pred_ok"] for r in runs), sum(r["n"] for r in runs))
        frag = sum(r["fragmentation"] for r in runs)
        facts_str = f"{f_lo}-{f_hi}" if f_lo != f_hi else str(f_lo)
        print(f"{model[:43]:<44}{facts_str:>8}{t_mean:>7.0f}s{span:>7}{comp:>9}{kinds:>7}{pred:>7}{frag:>6}")
    print("\nLecture : faits~ = min-max sur les runs (large = instable) ; span/complet/kinds/pred "
          "= % poolé sur tous les faits ; frag = formes d'entité fusionnées (total).")


if __name__ == "__main__":
    asyncio.run(main())
