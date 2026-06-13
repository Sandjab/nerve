# tests/test_pipeline.py
import nerve.pipeline as pipe
from nerve.config import load_config
from nerve.store import Store

async def fake_stream(cfg, messages, **kw):
    for ch in ['[{"subject":"A","predicate":"r","object":"B"}',
               ',{"subject":"C","predicate":"s","object":"D"}]']:
        yield ch

async def test_run_extraction_stores_and_emits(tmp_path, monkeypatch):
    monkeypatch.setattr(pipe, "stream_chat", fake_stream)
    st = Store(str(tmp_path / "p.db"), embed_dim=8); st.init_db()
    set_id = st.create_set("S"); doc_id = st.create_document(set_id, "d", "text")
    cfg = load_config()
    events = [e async for e in pipe.run_extraction(cfg, st, doc_id, "un texte")]
    facts = [e for e in events if e["type"] == "fact"]
    assert len(facts) == 2
    assert events[-1]["type"] == "done"
    assert len(st.get_facts(doc_id)) == 2
    assert st.get_document(doc_id)["status"] == "done"
