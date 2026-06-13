# tests/test_store.py
from nerve.store import Store

def test_create_and_read_facts(tmp_path):
    st = Store(str(tmp_path / "t.db"), embed_dim=8)
    st.init_db()
    set_id = st.create_set("Scriptorium")
    doc_id = st.create_document(set_id, title="doc", source_kind="text")
    f1 = st.add_fact(doc_id, {"subject": "A", "predicate": "r", "object": "B",
                              "confidence": 90, "tags": ["x"]})
    st.add_fact(doc_id, {"subject": "C", "predicate": "s", "object": "D"})
    facts = st.get_facts(doc_id)
    assert len(facts) == 2
    assert facts[0]["subject"] == "A"
    assert facts[0]["tags"] == ["x"]
    assert isinstance(f1, int)
    st.finish_document(doc_id)
    assert st.get_document(doc_id)["status"] == "done"

def test_sqlite_vec_loads(tmp_path):
    """Valide tôt le risque : chargement de l'extension sqlite-vec sur cette machine."""
    st = Store(str(tmp_path / "v.db"), embed_dim=8)
    st.init_db()
    cur = st.conn.execute("SELECT name FROM sqlite_master WHERE name='vec_facts'")
    assert cur.fetchone() is not None

def test_entities_crud_and_vectors(tmp_path):
    st = Store(str(tmp_path / "e.db"), embed_dim=4)
    st.init_db()
    set_id = st.create_set("S"); doc_id = st.create_document(set_id, "d", "text")
    eid = st.create_entity(doc_id, canonical_name="Cluny", normalized_key="cluny")
    assert isinstance(eid, int)
    assert st.find_entity_by_key(doc_id, "cluny") == eid
    assert st.find_entity_by_key(doc_id, "absent") is None
    st.add_entity_vector(eid, [1.0, 0.0, 0.0, 0.0])      # dim 4
    st.bump_entity_mention(eid)
    st.set_entity_canonical(eid, "Abbaye de Cluny")
    # vec_entities a bien 1 ligne
    n = st.conn.execute("SELECT count(*) FROM vec_entities").fetchone()[0]
    assert n == 1

def test_vec_facts_is_populated(tmp_path):
    st = Store(str(tmp_path / "vf.db"), embed_dim=4)
    st.init_db()
    set_id = st.create_set("S"); doc_id = st.create_document(set_id, "d", "text")
    fid = st.add_fact(doc_id, {"subject": "A", "predicate": "r", "object": "B"})
    st.add_fact_vector(fid, [0.0, 1.0, 0.0, 0.0])
    n = st.conn.execute("SELECT count(*) FROM vec_facts").fetchone()[0]
    assert n == 1
