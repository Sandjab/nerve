# nerve/dedup.py
def dedup_text(fact: dict, field: str = "triple") -> str:
    s, p, o = fact.get("subject", ""), fact.get("predicate", ""), fact.get("object", "")
    t, d = fact.get("title", ""), fact.get("description", "")
    if field == "title":
        return t
    if field == "description":
        return d
    if field == "title+desc":
        return f"{t} {d}".strip()
    if field == "all":
        return f"{t} {d} {s} {p} {o}".strip()
    return f"{s} {p} {o}".strip()   # défaut : triple

def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))

class FactDeduper:
    """Dedup intra-document de faits : working set en mémoire (vecteurs normalisés),
    cosinus = produit scalaire. check() embed le texte de dedup ; add() retient un
    fait non-dup avec son id."""

    def __init__(self, embed_fn, threshold: float, field: str = "triple"):
        self.embed_fn = embed_fn        # async (text) -> list[float]
        self.threshold = threshold
        self.field = field
        self._retained: list[tuple[int, list[float]]] = []   # (fact_id, vec)

    async def check(self, fact: dict) -> tuple[bool, int | None, list[float]]:
        vec = await self.embed_fn(dedup_text(fact, self.field))
        best_id, best_sim = None, 0.0
        for fid, fvec in self._retained:
            sim = _dot(vec, fvec)
            if sim > best_sim:
                best_id, best_sim = fid, sim
        if best_sim >= self.threshold:
            return True, best_id, vec
        return False, None, vec

    def add(self, fact_id: int, vec: list[float]) -> None:
        self._retained.append((fact_id, vec))
