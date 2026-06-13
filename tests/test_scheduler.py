import asyncio
import nerve.scheduler as sched_mod
from nerve.scheduler import write_segments, load_segments, Scheduler
from nerve.store import Store
from nerve.config import load_config

def test_segments_roundtrip(tmp_path):
    segs = [("texte un", ""), ("texte deux", "b.txt")]
    write_segments(str(tmp_path), 7, segs)
    assert load_segments(str(tmp_path), 7) == segs

async def fake_run_done(cfg, store, doc_id, segments, *, start_segment=0, start_chunk=0, client=None):
    yield {"type": "fact", "fact": {"subject": "A", "predicate": "r", "object": "B", "id": 1},
           "is_duplicate": False, "source_file": ""}
    yield {"type": "round_end", "segment": 0, "chunk": 0, "source_file": ""}
    store.finish_document(doc_id)
    d = store.get_document(doc_id)
    yield {"type": "done", "total_facts": d["total_facts"],
           "unique_facts": d["unique_facts"], "duplicate_facts": d["duplicate_facts"]}

async def test_worker_processes_and_marks_done(tmp_path):
    st = Store(str(tmp_path / "w.db"), embed_dim=2); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text")
    write_segments(str(tmp_path), doc_id, [("texte", "")])
    sched = Scheduler(load_config(), st, run=fake_run_done, data_dir=str(tmp_path))
    sub = sched.subscribe(doc_id)
    sched.start(); sched.enqueue(doc_id)
    types = []
    try:
        while True:
            ev = await asyncio.wait_for(sub.get(), timeout=2)
            types.append(ev.get("type"))
            if ev.get("type") == "done":
                break
    finally:
        await sched.stop()
    assert "fact" in types and "done" in types
    doc = st.get_document(doc_id)
    assert doc["status"] == "done"
    assert doc["progress_chunk"] == 1          # set_progress(0, 0+1) sur round_end
