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

def test_add_fact_counts_and_entities(tmp_path):
    st = Store(str(tmp_path / "c.db"), embed_dim=4)
    st.init_db()
    set_id = st.create_set("S"); doc_id = st.create_document(set_id, "d", "text")
    e1 = st.create_entity(doc_id, "A", "a"); e2 = st.create_entity(doc_id, "B", "b")
    st.add_fact(doc_id, {"subject": "A", "predicate": "r", "object": "B"},
                subject_entity_id=e1, object_entity_id=e2)
    st.add_fact(doc_id, {"subject": "A", "predicate": "r", "object": "B"},
                is_duplicate=True, dup_of_id=1)
    doc = st.get_document(doc_id)
    assert doc["total_facts"] == 2
    assert doc["unique_facts"] == 1
    assert doc["duplicate_facts"] == 1
    facts = st.get_facts(doc_id)                 # par défaut : non-dup seulement
    assert len(facts) == 1
    assert facts[0]["subject_canonical"] == "A"  # nom canonique via l'entité

def test_get_facts_can_include_duplicates(tmp_path):
    st = Store(str(tmp_path / "d.db"), embed_dim=4)
    st.init_db()
    set_id = st.create_set("S"); doc_id = st.create_document(set_id, "d", "text")
    st.add_fact(doc_id, {"subject": "A", "predicate": "r", "object": "B"})
    st.add_fact(doc_id, {"subject": "A", "predicate": "r", "object": "B"},
                is_duplicate=True, dup_of_id=1)
    assert len(st.get_facts(doc_id)) == 1
    assert len(st.get_facts(doc_id, include_duplicates=True)) == 2

def test_wal_enabled(tmp_path):
    st = Store(str(tmp_path / "wal.db"), embed_dim=4); st.init_db()
    assert st.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"

def test_status_and_progress(tmp_path):
    st = Store(str(tmp_path / "p.db"), embed_dim=4); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text")
    assert st.get_document(doc_id)["progress_segment"] == 0
    assert st.get_document(doc_id)["progress_chunk"] == 0
    st.set_status(doc_id, "queued"); assert st.get_document(doc_id)["status"] == "queued"
    st.set_progress(doc_id, 2, 5)
    doc = st.get_document(doc_id)
    assert doc["progress_segment"] == 2 and doc["progress_chunk"] == 5

def test_list_resumable(tmp_path):
    st = Store(str(tmp_path / "lr.db"), embed_dim=4); st.init_db()
    s = st.create_set("S")
    d1 = st.create_document(s, "1", "text"); st.set_status(d1, "running")
    d2 = st.create_document(s, "2", "text"); st.set_status(d2, "queued")
    d3 = st.create_document(s, "3", "text"); st.finish_document(d3)  # done
    d4 = st.create_document(s, "4", "text"); st.set_status(d4, "paused")
    assert st.list_resumable() == [d1, d2]
