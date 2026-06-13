# tests/test_api.py
import io
import json
import zipfile
import os
import nerve.pipeline as pipe
from fastapi.testclient import TestClient

async def fake_stream(cfg, messages, **kw):
    yield ('[{"subject":"Chat","predicate":"dort_sur","object":"Tapis"},'
           '{"subject":"Chat","predicate":"dort_sur","object":"Tapis"}]')  # 2e = doublon

async def fake_embed(cfg, texts, **kw):
    base = {"Chat": [1.0, 0.0], "Tapis": [0.0, 1.0],
            "Chat dort_sur Tapis": [1.0, 0.0]}
    return [base.get(t, [0.5, 0.5]) for t in texts]

def test_create_document_enqueues(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")
    import importlib, nerve.api as api
    importlib.reload(api)
    client = TestClient(api.app)                 # sans 'with' -> worker non démarré
    r = client.post("/api/documents", json={"title": "t", "text": "le chat dort"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    doc_id = body["document_id"]
    assert client.get(f"/api/documents/{doc_id}").json()["status"] == "queued"
    seg = os.path.join(str(tmp_path), "inputs", str(doc_id), "segments.jsonl")
    assert os.path.exists(seg)

def test_get_facts_unknown_document_returns_404(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    import importlib, nerve.api as api
    importlib.reload(api)
    client = TestClient(api.app)
    assert client.get("/api/documents/9999/facts").status_code == 404

async def fake_transcode(cfg, url, *, client=None):
    return ("# Page\n\ncorps de la page", "Page")

def test_create_document_from_url_enqueues(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")
    import importlib, nerve.api as api
    importlib.reload(api)
    monkeypatch.setattr(api, "transcode_url", fake_transcode)
    client = TestClient(api.app)
    r = client.post("/api/documents", json={"url": "https://ex.com/a"})
    assert r.status_code == 200 and r.json()["status"] == "queued"
    doc_id = r.json()["document_id"]
    doc = client.get(f"/api/documents/{doc_id}").json()
    assert doc["source_kind"] == "url" and doc["source_ref"] == "https://ex.com/a"
    assert json.loads(doc["params_json"])["dedup_field"] == api.cfg.dedup_field

def test_create_document_requires_text_or_url(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    import importlib, nerve.api as api
    importlib.reload(api)
    client = TestClient(api.app)
    assert client.post("/api/documents", json={}).status_code == 400

def test_create_document_url_transcode_failure_422(tmp_path, monkeypatch):
    # URL invalide / tous transcodeurs en échec -> erreur d'input client -> 422, pas 500
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")
    import importlib, nerve.api as api
    importlib.reload(api)
    async def boom(cfg, url, *, client=None):
        raise RuntimeError("tous les transcodeurs ont échoué")
    monkeypatch.setattr(api, "transcode_url", boom)
    client = TestClient(api.app)
    assert client.post("/api/documents", json={"url": "http://invalide"}).status_code == 422

def test_upload_zip_enqueues(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")
    import importlib, nerve.api as api
    importlib.reload(api)
    client = TestClient(api.app)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.txt", "le chat dort")
        z.writestr("bad.txt", "")
    r = client.post("/api/documents/upload",
                    files={"file": ("c.zip", buf.getvalue(), "application/zip")},
                    data={"set_name": "S"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued" and body["skipped"] == ["bad.txt"]
    doc = client.get(f"/api/documents/{body['document_id']}").json()
    assert doc["source_kind"] == "file" and doc["source_ref"] == "c.zip"

def test_upload_unreadable_file_fails_loud(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")
    import importlib, nerve.api as api
    importlib.reload(api)
    client = TestClient(api.app)
    r = client.post("/api/documents/upload",
                    files={"file": ("vide.txt", b"   ", "text/plain")},
                    data={"set_name": "S"})
    assert r.status_code == 422

def test_upload_corrupt_zip_fails_loud(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")
    import importlib, nerve.api as api
    importlib.reload(api)
    client = TestClient(api.app)
    r = client.post("/api/documents/upload",
                    files={"file": ("c.zip", b"pas un zip", "application/zip")},
                    data={"set_name": "S"})
    assert r.status_code == 422
    last = api.store.conn.execute(
        "SELECT status FROM documents ORDER BY id DESC LIMIT 1").fetchone()
    assert last["status"] == "failed"      # le document n'est pas laissé en 'running'

def test_pause_resume_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")
    import importlib, nerve.api as api
    importlib.reload(api)
    client = TestClient(api.app)
    doc_id = api.store.create_document(api.store.create_set("S"), "d", "text")
    api.store.set_status(doc_id, "queued")
    assert client.post(f"/api/documents/{doc_id}/pause").json()["status"] == "paused"
    assert client.post(f"/api/documents/{doc_id}/resume").json()["status"] == "queued"
    assert client.post("/api/documents/9999/pause").status_code == 404

def test_sse_replay_for_done_document(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")
    import importlib, nerve.api as api
    importlib.reload(api)
    doc_id = api.store.create_document(api.store.create_set("S"), "d", "text")
    api.store.add_fact(doc_id, {"subject": "A", "predicate": "r", "object": "B"})
    api.store.finish_document(doc_id)                 # statut done -> le flux se termine seul
    client = TestClient(api.app)
    with client.stream("GET", f"/api/documents/{doc_id}/events") as r:
        body = "".join(r.iter_text())
    assert '"type": "replay"' in body
    assert '"type": "status"' in body
    assert '"type": "done"' in body                   # event terminal -> le client ferme proprement
    assert '"A"' in body                              # le fait rejoué est présent
    assert client.get("/api/documents/9999/events").status_code == 404

def test_sse_terminal_event_for_failed_document(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")
    import importlib, nerve.api as api
    importlib.reload(api)
    doc_id = api.store.create_document(api.store.create_set("S"), "d", "text")
    api.store.finish_document(doc_id, error="boom")   # statut failed
    client = TestClient(api.app)
    with client.stream("GET", f"/api/documents/{doc_id}/events") as r:
        body = "".join(r.iter_text())
    assert '"type": "error"' in body                  # event terminal d'erreur
    assert "boom" in body

def test_sets_list_and_detail(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "3")
    import importlib, nerve.api as api
    importlib.reload(api)
    s = api.store.create_set("S")
    api.store.create_document(s, "d", "text")
    client = TestClient(api.app)
    lst = client.get("/api/sets").json()
    assert any(x["id"] == s and x["document_count"] == 1 for x in lst)
    detail = client.get(f"/api/sets/{s}").json()
    assert detail["name"] == "S" and len(detail["documents"]) == 1
    assert client.get("/api/sets/9999").status_code == 404
