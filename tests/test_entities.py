# tests/test_entities.py
from collections import Counter
from nerve.store import Store
from nerve.entities import normalized_key, lexical_guard, EntityResolver

def test_normalized_key_collapses_variants():
    assert normalized_key("Cluny_Abbey") == "cluny abbey"
    assert normalized_key("  Saint-Gall ") == "saint gall"
    assert normalized_key("Église") == "eglise"
    assert normalized_key("Cluny_Abbey") == normalized_key("Cluny Abbey")

def test_lexical_guard():
    assert lexical_guard("cluny", "cluny abbey")        # sous-chaîne
    assert lexical_guard("cluny abbaye", "abbaye de cluny")  # sous-ensemble de tokens (réordonné)
    assert lexical_guard("union europeenne", "ue")      # acronyme
    assert not lexical_guard("cluny", "paris")          # aucun lien lexical
    # tokens partagés (notker, le) mais aucun sous-ensemble -> NE PAS autoriser la fusion
    assert not lexical_guard("notker le begue", "notker le chauve")

def _emb(mapping):
    async def embed_fn(text):
        return mapping[text]
    return embed_fn

async def test_resolver_merges_by_lexical_key(tmp_path):
    st = Store(str(tmp_path / "r.db"), embed_dim=2); st.init_db()
    doc = st.create_document(st.create_set("S"), "d", "text")
    # le 1er resolve embed toujours (création) ; le 2e (même clé) saute l'embed
    r = EntityResolver(st, doc, _emb({"Cluny_Abbey": [1.0, 0.0]}), threshold=0.9)
    a = await r.resolve("Cluny_Abbey")
    b = await r.resolve("Cluny Abbey")   # clé "cluny abbey" déjà connue -> même entité
    assert a == b

async def test_resolver_hybrid_guard_blocks_false_merge(tmp_path):
    st = Store(str(tmp_path / "g.db"), embed_dim=2); st.init_db()
    doc = st.create_document(st.create_set("S"), "d", "text")
    # cosinus élevé (0.99) mais aucun lien lexical -> NE PAS fusionner
    emb = _emb({"Cluny": [1.0, 0.0], "Paris": [0.99, 0.1411]})
    r = EntityResolver(st, doc, emb, threshold=0.9)
    a = await r.resolve("Cluny")
    b = await r.resolve("Paris")
    assert a != b

async def test_resolver_merges_on_embedding_plus_lexical(tmp_path):
    st = Store(str(tmp_path / "m.db"), embed_dim=2); st.init_db()
    doc = st.create_document(st.create_set("S"), "d", "text")
    emb = _emb({"Cluny": [1.0, 0.0], "Cluny abbaye": [0.97, 0.2429]})  # cos ~0.97
    r = EntityResolver(st, doc, emb, threshold=0.9)
    a = await r.resolve("Cluny")
    b = await r.resolve("Cluny abbaye")   # clé differe, mais cos>=seuil ET garde lexical OK
    assert a == b

async def test_resolver_preload_reuses_known_entity():
    class _Store:                                       # store minimal (pas de DB)
        def bump_entity_mention(self, eid): pass
        def set_entity_canonical(self, eid, name): pass
    async def emb(s): return [1.0, 0.0]
    r = EntityResolver(_Store(), 1, emb, 0.75)
    r.preload([(42, "Cluny", "cluny", 3, [1.0, 0.0])])
    eid = await r.resolve("Cluny")                      # clé "cluny" connue -> réutilise 42
    assert eid == 42
    assert r._surface[42] == Counter({"Cluny": 4})      # mention pré-chargée + 1
