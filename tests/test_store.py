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

def test_facts_entity_ids_sont_indexes(tmp_path):
    # subject_entity_id / object_entity_id sont filtrés (facts_for_entities : IN)
    # et joints (get_facts, facts_for_set) ; sans index -> scan complet de facts.
    # Cohérent avec idx_facts_doc déjà posé sur la FK document_id.
    st = Store(str(tmp_path / "idx.db"), embed_dim=4)
    st.init_db()
    names = {r["name"] for r in st.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "idx_facts_subject" in names
    assert "idx_facts_object" in names

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

def test_get_facts_n_expose_pas_les_entity_ids_internes(tmp_path):
    # subject_entity_id / object_entity_id sont des PK SQLite internes, sans
    # sens côté client ; get_facts ne doit pas les fuiter (libellés + *_canonical
    # suffisent au front). Régresse si on revient à un SELECT f.* brut non filtré.
    st = Store(str(tmp_path / "ids.db"), embed_dim=4)
    st.init_db()
    set_id = st.create_set("S"); doc_id = st.create_document(set_id, "d", "text")
    e1 = st.create_entity(doc_id, "A", "a"); e2 = st.create_entity(doc_id, "B", "b")
    st.add_fact(doc_id, {"subject": "A", "predicate": "r", "object": "B"},
                subject_entity_id=e1, object_entity_id=e2)
    fact = st.get_facts(doc_id)[0]
    assert "subject_entity_id" not in fact
    assert "object_entity_id" not in fact
    # champs utiles au client conservés
    assert fact["subject"] == "A" and fact["object"] == "B"
    assert fact["subject_canonical"] == "A" and fact["object_canonical"] == "B"

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

def test_load_fact_vectors(tmp_path):
    st = Store(str(tmp_path / "lfv.db"), embed_dim=3); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text")
    f1 = st.add_fact(doc_id, {"subject": "A", "predicate": "r", "object": "B"})
    st.add_fact_vector(f1, [0.1, 0.2, 0.3])
    f2 = st.add_fact(doc_id, {"subject": "A", "predicate": "r", "object": "B"},
                     is_duplicate=True, dup_of_id=f1)            # dup -> pas de vecteur
    rows = st.load_fact_vectors(doc_id)
    assert len(rows) == 1
    fid, vec = rows[0]
    assert fid == f1
    assert all(abs(a - b) < 1e-6 for a, b in zip(vec, [0.1, 0.2, 0.3]))

def test_load_entities(tmp_path):
    st = Store(str(tmp_path / "le.db"), embed_dim=2); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text")
    eid = st.create_entity(doc_id, "Cluny", "cluny"); st.add_entity_vector(eid, [1.0, 0.0])
    st.bump_entity_mention(eid)
    rows = st.load_entities(doc_id)
    assert len(rows) == 1
    rid, canonical, key, mention, vec = rows[0]
    assert (rid, canonical, key) == (eid, "Cluny", "cluny")
    assert mention == 2
    assert all(abs(a - b) < 1e-6 for a, b in zip(vec, [1.0, 0.0]))

def test_list_sets_counts_documents(tmp_path):
    st = Store(str(tmp_path / "ls.db"), embed_dim=3); st.init_db()
    s1 = st.create_set("Alpha"); s2 = st.create_set("Beta")
    st.create_document(s1, "d1", "text"); st.create_document(s1, "d2", "text")
    rows = st.list_sets()
    by_id = {r["id"]: r for r in rows}
    assert by_id[s1]["name"] == "Alpha" and by_id[s1]["document_count"] == 2
    assert by_id[s2]["document_count"] == 0

def test_get_set_with_documents(tmp_path):
    st = Store(str(tmp_path / "gs.db"), embed_dim=3); st.init_db()
    s = st.create_set("S")
    d = st.create_document(s, "doc", "text")
    out = st.get_set(s)
    assert out["name"] == "S"
    assert [doc["id"] for doc in out["documents"]] == [d]
    assert st.get_set(9999) is None

def _seed_fact(st, doc_id, s_name, s_key, pred, o_name, o_key, conf=80):
    se = st.create_entity(doc_id, s_name, s_key)
    oe = st.create_entity(doc_id, o_name, o_key)
    return st.add_fact(doc_id, {"subject": s_name, "predicate": pred,
                                "object": o_name, "confidence": conf},
                       subject_entity_id=se, object_entity_id=oe)

def test_facts_for_set_enriched_rows(tmp_path):
    st = Store(str(tmp_path / "ffs.db"), embed_dim=3); st.init_db()
    s = st.create_set("S")
    d = st.create_document(s, "doc", "text")
    _seed_fact(st, d, "Cluny", "cluny", "fonde", "Abbaye", "abbaye", conf=90)
    _seed_fact(st, d, "Cluny", "cluny", "situe", "Bourgogne", "bourgogne", conf=40)
    rows = st.facts_for_set(s)
    assert len(rows) == 2
    r0 = rows[0]
    assert r0["s_key"] == "cluny" and r0["s_name"] == "Cluny"
    assert r0["o_key"] == "abbaye" and r0["predicate"] == "fonde"
    # filtre min_conf
    assert len(st.facts_for_set(s, min_conf=50)) == 1

def test_search_facts_knn_and_set_filter(tmp_path):
    st = Store(str(tmp_path / "sf.db"), embed_dim=3); st.init_db()
    sa = st.create_set("A"); sb = st.create_set("B")
    da = st.create_document(sa, "da", "text"); db = st.create_document(sb, "db", "text")
    fa = st.add_fact(da, {"subject": "A", "predicate": "r", "object": "B"})
    st.add_fact_vector(fa, [1.0, 0.0, 0.0])
    fb = st.add_fact(db, {"subject": "C", "predicate": "r", "object": "D"})
    st.add_fact_vector(fb, [0.0, 1.0, 0.0])
    # requête proche de fa
    res = st.search_facts([1.0, 0.0, 0.0], k=1)
    assert res[0]["fact_id"] == fa and "distance" in res[0]
    # filtre set B alors que le + proche est fa (set A) -> on récupère fb (over-fetch)
    res_b = st.search_facts([1.0, 0.0, 0.0], k=1, sets=[sb])
    assert [r["fact_id"] for r in res_b] == [fb]
    assert res_b[0]["set_id"] == sb

def test_entities_by_key_cross_document(tmp_path):
    st = Store(str(tmp_path / "ebk.db"), embed_dim=3); st.init_db()
    s = st.create_set("S")
    d1 = st.create_document(s, "d1", "text"); d2 = st.create_document(s, "d2", "text")
    e1 = st.create_entity(d1, "Cluny", "cluny")
    e2 = st.create_entity(d2, "cluny", "cluny")
    st.create_entity(d1, "Autre", "autre")
    got = {e["id"] for e in st.entities_by_key("cluny")}
    assert got == {e1, e2}

def test_entity_neighbors_knn(tmp_path):
    st = Store(str(tmp_path / "en.db"), embed_dim=3); st.init_db()
    s = st.create_set("S"); d = st.create_document(s, "d", "text")
    e1 = st.create_entity(d, "Cluny", "cluny"); st.add_entity_vector(e1, [1.0, 0.0, 0.0])
    e2 = st.create_entity(d, "Loin", "loin"); st.add_entity_vector(e2, [0.0, 1.0, 0.0])
    res = st.entity_neighbors([1.0, 0.0, 0.0], k=1)
    assert res[0]["entity_id"] == e1 and res[0]["normalized_key"] == "cluny"

def test_facts_for_entities(tmp_path):
    st = Store(str(tmp_path / "ffe.db"), embed_dim=3); st.init_db()
    s = st.create_set("S"); d = st.create_document(s, "d", "text")
    se = st.create_entity(d, "Cluny", "cluny"); oe = st.create_entity(d, "Abbaye", "abbaye")
    other = st.create_entity(d, "X", "x")
    f = st.add_fact(d, {"subject": "Cluny", "predicate": "est", "object": "Abbaye"},
                    subject_entity_id=se, object_entity_id=oe)
    st.add_fact(d, {"subject": "X", "predicate": "p", "object": "X"},
                subject_entity_id=other, object_entity_id=other)
    rows = st.facts_for_entities([se])
    assert [r["fact_id"] for r in rows] == [f]
    assert rows[0]["s_key"] == "cluny" and rows[0]["o_key"] == "abbaye"

def test_graph_cols_expose_kind_and_set(tmp_path):
    st = Store(str(tmp_path / "gc.db"), embed_dim=3); st.init_db()
    s = st.create_set("S"); d = st.create_document(s, "doc", "text")
    se = st.create_entity(d, "Cluny", "cluny", kind="entity")
    oe = st.create_entity(d, "910", "910", kind="value")
    f = st.add_fact(d, {"subject": "Cluny", "predicate": "fonde", "object": "910"},
                    subject_entity_id=se, object_entity_id=oe)
    r0 = st.facts_for_set(s)[0]
    assert r0["s_kind"] == "entity" and r0["o_kind"] == "value" and r0["set_id"] == s
    # facts_for_entities expose aussi set_id (JOIN documents ajouté)
    r1 = st.facts_for_entities([se])[0]
    assert r1["set_id"] == s and r1["s_kind"] == "entity" and r1["fact_id"] == f

def test_entity_kind_default_and_promote(tmp_path):
    st = Store(str(tmp_path / "kind.db"), embed_dim=3); st.init_db()
    d = st.create_document(st.create_set("S"), "doc", "text")
    def kind_of(eid):
        return st.conn.execute("SELECT kind FROM entities WHERE id=?", (eid,)).fetchone()[0]
    e_def = st.create_entity(d, "Cluny", "cluny")               # défaut -> entity
    e_val = st.create_entity(d, "910", "910", kind="value")     # explicite value
    assert kind_of(e_def) == "entity" and kind_of(e_val) == "value"
    st.promote_entity_kind(e_val)                               # value -> entity (idempotent)
    assert kind_of(e_val) == "entity"
    st.promote_entity_kind(e_def)                               # déjà entity -> no-op
    assert kind_of(e_def) == "entity"
