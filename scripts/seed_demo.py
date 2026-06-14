"""Jeu de démonstration pour les captures du manuel (docs/manuel.html).

Insère 2 sets / 3 documents avec des entités partagées et de VRAIS vecteurs
bge-m3, SANS passer par le LLM (déterministe, rapide, reproductible). L'entité
« Cluny » est partagée par les 3 documents : elle illustre le *collapse*
(plusieurs documents d'un même set → un seul nœud) et les *passerelles*
(la même entité reliant deux sets, surlignée en bordeaux dans la vue Transverse).

Usage :
    rm -f data/nerve.db*           # repartir d'un schéma frais
    uv run python scripts/seed_demo.py
    uv run nerve                   # puis http://127.0.0.1:3000

Prérequis : Ollama servant bge-m3 (EMBED_MODEL), cf. config par défaut.
"""
import asyncio
import httpx

from nerve.config import load_config
from nerve.store import Store
from nerve.embeddings import embed
from nerve.entities import normalized_key

# Libellés traités comme des *valeurs* (dates, quantités) → kind='value' pour le mode Type.
VALUES = {"910", "1077", "soixante ans", "mille monastères"}

# (nom du set, [ (titre du document, [ (sujet, prédicat, objet, confiance, description) ]) ])
DATA = [
    ("Abbaye de Cluny", [
        ("Fondation de Cluny", [
            ("Cluny", "fondée par", "Guillaume Ier d'Aquitaine", 95,
             "Le duc Guillaume Ier d'Aquitaine fonde l'abbaye de Cluny et la place sous la protection directe du Saint-Siège."),
            ("Cluny", "fondée en", "910", 92,
             "La charte de fondation de l'abbaye de Cluny est datée de l'an 910."),
            ("Cluny", "suit", "règle de saint Benoît", 88,
             "La communauté de Cluny observe la règle de saint Benoît, socle du monachisme bénédictin."),
            ("Bernon", "premier abbé de", "Cluny", 90,
             "Bernon de Baume devient le premier abbé de Cluny lors de sa fondation."),
            ("Cluny", "située en", "Bourgogne", 85,
             "L'abbaye de Cluny est implantée en Bourgogne, dans l'actuelle Saône-et-Loire."),
            ("Guillaume Ier d'Aquitaine", "porte le titre de", "duc d'Aquitaine", 80,
             "Guillaume Ier, dit le Pieux, est duc d'Aquitaine et comte d'Auvergne."),
        ]),
        ("L'apogée clunisien", [
            ("Cluny", "dirigée par", "Odon de Cluny", 90,
             "Odon de Cluny, deuxième abbé, étend l'influence spirituelle de l'abbaye au Xe siècle."),
            ("Cluny", "dirigée par", "Hugues de Semur", 91,
             "Hugues de Semur, abbé pendant six décennies, conduit Cluny à son apogée."),
            ("Hugues de Semur", "gouverne pendant", "soixante ans", 78,
             "L'abbatiat de Hugues de Semur dure près de soixante ans (1049-1109)."),
            ("Cluny", "rayonne sur", "ordre clunisien", 89,
             "Cluny est la tête d'un vaste réseau de prieurés formant l'ordre clunisien."),
            ("ordre clunisien", "compte", "mille monastères", 75,
             "À son apogée, l'ordre clunisien fédère près de mille monastères à travers l'Europe."),
            ("Odon de Cluny", "réforme", "vie monastique", 83,
             "Odon de Cluny promeut une réforme rigoureuse de la vie monastique."),
        ]),
    ]),
    ("Réforme grégorienne", [
        ("La réforme grégorienne", [
            ("réforme grégorienne", "menée par", "Grégoire VII", 93,
             "La réforme grégorienne, qui réaffirme l'autorité pontificale, est menée par le pape Grégoire VII."),
            ("Grégoire VII", "formé à", "Cluny", 82,
             "Avant son pontificat, Grégoire VII (Hildebrand) est marqué par l'esprit réformateur de Cluny."),
            ("réforme grégorienne", "combat", "simonie", 87,
             "La réforme grégorienne s'attaque à la simonie, le commerce des charges ecclésiastiques."),
            ("réforme grégorienne", "déclenche", "querelle des Investitures", 90,
             "Le conflit sur la nomination des évêques ouvre la querelle des Investitures."),
            ("querelle des Investitures", "oppose", "Henri IV", 88,
             "La querelle des Investitures oppose la papauté à l'empereur Henri IV."),
            ("Henri IV", "s'humilie à", "Canossa", 91,
             "Excommunié, l'empereur Henri IV vient implorer le pardon du pape à Canossa."),
            ("Canossa", "a lieu en", "1077", 92,
             "La pénitence de Canossa se déroule durant l'hiver 1077."),
        ]),
    ]),
]


async def main() -> None:
    cfg = load_config()
    store = Store(cfg.db_path, cfg.embed_dim)
    store.init_db()

    async with httpx.AsyncClient(timeout=None) as client:
        for set_name, docs in DATA:
            set_id = store.create_set(set_name)
            for title, facts in docs:
                doc_id = store.create_document(set_id, title, "text")
                ent_ids: dict[str, int] = {}        # normalized_key -> entity_id (par document)

                def ent(name: str) -> int:
                    key = normalized_key(name)
                    if key not in ent_ids:
                        kind = "value" if name in VALUES else "entity"
                        ent_ids[key] = store.create_entity(doc_id, name, key, kind)
                    return ent_ids[key]

                fact_ids: list[int] = []
                for subj, pred, obj, conf, desc in facts:
                    sid, oid = ent(subj), ent(obj)
                    fid = store.add_fact(
                        doc_id,
                        {"subject": subj, "predicate": pred, "object": obj,
                         "title": f"{subj} — {pred} — {obj}", "description": desc,
                         "confidence": conf},
                        subject_entity_id=sid, object_entity_id=oid)
                    fact_ids.append(fid)

                # Vecteurs d'entités (canonical_name) et de faits (triplet + description).
                keys = list(ent_ids)
                ent_vecs = await embed(cfg.embed, keys, client=client)
                for key, vec in zip(keys, ent_vecs):
                    store.add_entity_vector(ent_ids[key], vec)

                fact_texts = [f"{s} {p} {o}. {d}" for s, p, o, _, d in facts]
                fact_vecs = await embed(cfg.embed, fact_texts, client=client)
                for fid, vec in zip(fact_ids, fact_vecs):
                    store.add_fact_vector(fid, vec)

                store.finish_document(doc_id)
                print(f"  set «{set_name}» / doc «{title}» : {len(facts)} faits, {len(keys)} entités")

    print("Seed terminé.")


if __name__ == "__main__":
    asyncio.run(main())
