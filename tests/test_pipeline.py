# tests/test_pipeline.py
import nerve.pipeline as pipe
from nerve.config import load_config
from nerve.store import Store

async def fake_stream(cfg, messages, **kw):
    # 3 faits : deux identiques (doublon) + un distinct ; entités "Cluny"/"Cluny_Abbey" fusionnent par clé
    yield ('[{"subject":"Cluny","predicate":"a_pour","object":"Scriptorium"},'
           '{"subject":"Cluny_Abbey","predicate":"a_pour","object":"Scriptorium"},'
           '{"subject":"Eudes","predicate":"copie","object":"Manuscrits"}]')

async def fake_embed(cfg, texts, **kw):
    # vecteurs déterministes : texte identique -> vecteur identique
    table = {
        "Cluny": [1.0, 0.0, 0.0], "Cluny_Abbey": [1.0, 0.0, 0.0],
        "Scriptorium": [0.0, 1.0, 0.0], "Eudes": [0.0, 0.0, 1.0],
        "Manuscrits": [0.7, 0.0, 0.714],
        "Cluny a_pour Scriptorium": [1.0, 0.0, 0.0],
        "Cluny_Abbey a_pour Scriptorium": [1.0, 0.0, 0.0],   # = au 1er -> doublon
        "Eudes copie Manuscrits": [0.0, 1.0, 0.0],
    }
    return [table[t] for t in texts]

async def test_run_extraction_dedups_and_resolves(tmp_path, monkeypatch):
    monkeypatch.setattr(pipe, "stream_chat", fake_stream)
    monkeypatch.setattr(pipe, "embed", fake_embed)
    st = Store(str(tmp_path / "p.db"), embed_dim=3); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text")
    cfg = load_config()
    events = [e async for e in pipe.run_extraction(cfg, st, doc_id, [("un texte", "")])]
    assert events[-1]["type"] == "done"
    doc = st.get_document(doc_id)
    assert doc["total_facts"] == 3
    assert doc["duplicate_facts"] == 1           # le 2e fait est un doublon du 1er
    assert doc["unique_facts"] == 2
    facts = st.get_facts(doc_id)                 # non-dup
    assert len(facts) == 2
    # 4 entités distinctes : Cluny(=Cluny_Abbey fusionné), Scriptorium, Eudes, Manuscrits.
    n_ent = st.conn.execute("SELECT count(*) FROM entities").fetchone()[0]
    assert n_ent == 4

async def fake_stream_one(cfg, messages, **kw):
    # le MÊME fait pour chaque segment -> le 2e (autre fichier) est un doublon
    yield '[{"subject":"Cluny","predicate":"a_pour","object":"Scriptorium"}]'

async def test_run_extraction_dedups_across_segments(tmp_path, monkeypatch):
    monkeypatch.setattr(pipe, "stream_chat", fake_stream_one)
    monkeypatch.setattr(pipe, "embed", fake_embed)      # réutilise la table de fake_embed
    st = Store(str(tmp_path / "seg.db"), embed_dim=3); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "file")
    cfg = load_config()
    segs = [("texte A", "a.txt"), ("texte B", "b.txt")]
    events = [e async for e in pipe.run_extraction(cfg, st, doc_id, segs)]
    assert events[-1]["type"] == "done"
    doc = st.get_document(doc_id)
    assert doc["total_facts"] == 2
    assert doc["unique_facts"] == 1
    assert doc["duplicate_facts"] == 1               # dedup traverse les 2 fichiers
    facts = st.get_facts(doc_id)                     # non-dup
    assert len(facts) == 1
    assert facts[0]["source_file"] == "a.txt"        # provenance du 1er segment
