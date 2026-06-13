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
