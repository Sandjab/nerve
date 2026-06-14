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

async def fake_run_two_chunks(cfg, store, doc_id, segments, *, start_segment=0, start_chunk=0, client=None):
    yield {"type": "round_end", "segment": 0, "chunk": 0, "source_file": ""}
    yield {"type": "round_end", "segment": 0, "chunk": 1, "source_file": ""}
    store.finish_document(doc_id)
    yield {"type": "done", "total_facts": 0, "unique_facts": 0, "duplicate_facts": 0}

async def test_pause_stops_at_round_end(tmp_path):
    st = Store(str(tmp_path / "pa.db"), embed_dim=2); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text")
    write_segments(str(tmp_path), doc_id, [("texte", "")])
    sched = Scheduler(load_config(), st, run=fake_run_two_chunks, data_dir=str(tmp_path))
    sched._pause.add(doc_id)                    # pause demandée avant exécution
    sub = sched.subscribe(doc_id)
    sched.start(); sched.enqueue(doc_id)
    try:
        while True:
            ev = await asyncio.wait_for(sub.get(), timeout=2)
            if ev.get("type") == "status" and ev.get("status") == "paused":
                break
    finally:
        await sched.stop()
    doc = st.get_document(doc_id)
    assert doc["status"] == "paused"
    assert doc["progress_chunk"] == 1           # arrêté après le 1er round_end (chunk 0 -> 1)

def test_resume_reenqueues(tmp_path):
    st = Store(str(tmp_path / "rq.db"), embed_dim=2); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text")
    st.set_status(doc_id, "paused")
    sched = Scheduler(load_config(), st, data_dir=str(tmp_path))
    sched.resume(doc_id)
    assert sched.queue.qsize() == 1
    assert st.get_document(doc_id)["status"] == "queued"

def test_pause_not_running_sets_paused(tmp_path):
    st = Store(str(tmp_path / "pn.db"), embed_dim=2); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text")
    st.set_status(doc_id, "queued")
    sched = Scheduler(load_config(), st, data_dir=str(tmp_path))
    sched.pause(doc_id)
    assert st.get_document(doc_id)["status"] == "paused"

def test_reconcile_reenqueues_interrupted(tmp_path):
    st = Store(str(tmp_path / "rc.db"), embed_dim=2); st.init_db()
    s = st.create_set("S")
    d1 = st.create_document(s, "1", "text"); st.set_status(d1, "running")
    d2 = st.create_document(s, "2", "text"); st.finish_document(d2)   # done
    sched = Scheduler(load_config(), st, data_dir=str(tmp_path))
    sched.reconcile()
    assert sched.queue.qsize() == 1
    assert st.get_document(d1)["status"] == "queued"
    assert st.get_document(d2)["status"] == "done"

async def test_pause_queued_doc_is_not_processed(tmp_path):
    st = Store(str(tmp_path / "pq.db"), embed_dim=2); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text")
    write_segments(str(tmp_path), doc_id, [("texte", "")])
    sched = Scheduler(load_config(), st, run=fake_run_done, data_dir=str(tmp_path))
    sched.enqueue(doc_id)          # queued, dans la file
    sched.pause(doc_id)            # doc queued -> paused, mais reste dans la file
    sched.start()
    await asyncio.wait_for(sched.queue.join(), timeout=2)  # attend le drainage par le worker
    await sched.stop()
    assert st.get_document(doc_id)["status"] == "paused"   # NE doit PAS valoir "done"

async def test_worker_marks_failed_when_process_raises(tmp_path):
    st = Store(str(tmp_path / "wf.db"), embed_dim=2); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text")
    # PAS de write_segments -> load_segments lèvera FileNotFoundError dans _process
    sched = Scheduler(load_config(), st, data_dir=str(tmp_path))
    sub = sched.subscribe(doc_id)
    sched.start(); sched.enqueue(doc_id)
    await asyncio.wait_for(sched.queue.join(), timeout=2)
    await sched.stop()
    assert st.get_document(doc_id)["status"] == "failed"
    types = []
    while not sub.empty():
        types.append(sub.get_nowait().get("type"))
    assert "error" in types

def test_resume_ignores_non_paused(tmp_path):
    st = Store(str(tmp_path / "rn.db"), embed_dim=2); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text")
    st.set_status(doc_id, "running")
    sched = Scheduler(load_config(), st, data_dir=str(tmp_path))
    sched.resume(doc_id)
    assert sched.queue.qsize() == 0                  # un doc non-paused n'est pas ré-enfilé
    assert st.get_document(doc_id)["status"] == "running"

def test_unsubscribe_removes_empty_key(tmp_path):
    st = Store(str(tmp_path / "us.db"), embed_dim=2); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text")
    sched = Scheduler(load_config(), st, data_dir=str(tmp_path))
    q = sched.subscribe(doc_id)
    assert doc_id in sched._subs
    sched.unsubscribe(doc_id, q)
    assert doc_id not in sched._subs                 # clé vidée -> purgée


async def _run_capturing(captured):
    async def fake_run(cfg, store, doc_id, segments, *, start_segment=0, start_chunk=0, client=None):
        captured["model"] = cfg.llm.model
        store.finish_document(doc_id)
        d = store.get_document(doc_id)
        yield {"type": "done", "total_facts": d["total_facts"],
               "unique_facts": d["unique_facts"], "duplicate_facts": d["duplicate_facts"]}
    return fake_run

async def _drain_until_done(sched, sub):
    try:
        while True:
            ev = await asyncio.wait_for(sub.get(), timeout=2)
            if ev.get("type") == "done":
                break
    finally:
        await sched.stop()

async def test_worker_applies_model_override(tmp_path):
    captured = {}
    st = Store(str(tmp_path / "ov.db"), embed_dim=2); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text", params={"model": "gemma4"})
    write_segments(str(tmp_path), doc_id, [("texte", "")])
    sched = Scheduler(load_config(), st, run=await _run_capturing(captured), data_dir=str(tmp_path))
    sub = sched.subscribe(doc_id)
    sched.start(); sched.enqueue(doc_id)
    await _drain_until_done(sched, sub)
    assert captured["model"] == "gemma4"

async def test_worker_uses_default_model_without_override(tmp_path):
    captured = {}
    st = Store(str(tmp_path / "def.db"), embed_dim=2); st.init_db()
    doc_id = st.create_document(st.create_set("S"), "d", "text")
    write_segments(str(tmp_path), doc_id, [("texte", "")])
    cfg = load_config()
    sched = Scheduler(cfg, st, run=await _run_capturing(captured), data_dir=str(tmp_path))
    sub = sched.subscribe(doc_id)
    sched.start(); sched.enqueue(doc_id)
    await _drain_until_done(sched, sub)
    assert captured["model"] == cfg.llm.model
