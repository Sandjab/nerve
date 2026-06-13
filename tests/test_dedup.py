# tests/test_dedup.py
from nerve.dedup import dedup_text, FactDeduper

def test_dedup_text_fields():
    f = {"subject": "A", "predicate": "r", "object": "B", "title": "T", "description": "D"}
    assert dedup_text(f, "triple") == "A r B"
    assert dedup_text(f, "title") == "T"
    assert dedup_text(f, "description") == "D"

def _emb(mapping):
    async def embed_fn(text):
        return mapping[text]
    return embed_fn

async def test_deduper_flags_duplicates_not_distinct():
    emb = _emb({"A r B": [1.0, 0.0], "C s D": [0.0, 1.0]})
    d = FactDeduper(emb, threshold=0.9, field="triple")
    is_dup, dup_of, vec = await d.check({"subject": "A", "predicate": "r", "object": "B"})
    assert is_dup is False and dup_of is None
    d.add(101, vec)                                   # 1er fait retenu, id 101
    is_dup2, dup_of2, _ = await d.check({"subject": "A", "predicate": "r", "object": "B"})
    assert is_dup2 is True and dup_of2 == 101         # identique -> doublon de 101
    is_dup3, dup_of3, vec3 = await d.check({"subject": "C", "predicate": "s", "object": "D"})
    assert is_dup3 is False and dup_of3 is None       # orthogonal -> distinct

async def test_deduper_preload_detects_known_dup():
    async def emb(s):
        return [1.0, 0.0] if s == "A r B" else [0.0, 1.0]
    d = FactDeduper(emb, 0.85, field="triple")
    d.preload([(7, [1.0, 0.0])])                       # un fait déjà retenu (fact_id=7)
    is_dup, dup_of, vec = await d.check({"subject": "A", "predicate": "r", "object": "B"})
    assert is_dup and dup_of == 7
