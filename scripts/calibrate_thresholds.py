# scripts/calibrate_thresholds.py
"""Affiche les cosinus bge-m3 sur des paires étiquetées pour choisir les seuils."""
import asyncio
from nerve.config import load_config
from nerve.embeddings import embed

# (texte_a, texte_b, devraient_fusionner?)
ENTITY_PAIRS = [
    ("Cluny", "Cluny Abbey", True),
    ("Cluny", "Abbaye de Cluny", True),
    ("Saint-Gall", "Abbaye de Saint-Gall", True),
    ("Notker le Bègue", "Notker le Chauve", False),
    ("Bernard de Clairvaux", "Hugues de Semur", False),
    ("Cluny", "Paris", False),
]
FACT_PAIRS = [
    ("Cluny a_pour_scriptorium Scriptorium", "Cluny possède un scriptorium", True),
    ("Eudes copie Manuscrits", "Othmar fonde Saint-Gall", False),
]

async def main():
    cfg = load_config()
    async def cos(a, b):
        va, vb = await embed(cfg.embed, [a, b])
        return sum(x * y for x, y in zip(va, vb))
    print("== entités (ENTITY_THRESHOLD) ==")
    for a, b, merge in ENTITY_PAIRS:
        print(f"  {await cos(a,b):.3f}  attendu={'fusion' if merge else 'distinct':8}  {a!r} / {b!r}")
    print("== faits (DEDUP_THRESHOLD) ==")
    for a, b, dup in FACT_PAIRS:
        print(f"  {await cos(a,b):.3f}  attendu={'dup' if dup else 'distinct':8}  {a!r} / {b!r}")

asyncio.run(main())
