# tests/test_api.py
import io
import json
import zipfile
import nerve.pipeline as pipe
from fastapi.testclient import TestClient

async def fake_stream(cfg, messages, **kw):
    yield ('[{"subject":"Chat","predicate":"dort_sur","object":"Tapis"},'
           '{"subject":"Chat","predicate":"dort_sur","object":"Tapis"}]')  # 2e = doublon

async def fake_embed(cfg, texts, **kw):
    base = {"Chat": [1.0, 0.0], "Tapis": [0.0, 1.0],
            "Chat dort_sur Tapis": [1.0, 0.0]}
    return [base.get(t, [0.5, 0.5]) for t in texts]

def test_create_document_and_get_facts(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")  # cohérent avec fake_embed (vecteurs 2D)
    monkeypatch.setattr(pipe, "stream_chat", fake_stream)
    monkeypatch.setattr(pipe, "embed", fake_embed)
    import importlib, nerve.api as api
    importlib.reload(api)
    client = TestClient(api.app)
    r = client.post("/api/documents", json={"title": "t", "text": "le chat dort"})
    assert r.status_code == 200
    body = r.json()
    doc_id = body["document_id"]
    assert body["total_facts"] == 2
    assert body["unique_facts"] == 1
    assert body["duplicate_facts"] == 1
    facts = client.get(f"/api/documents/{doc_id}/facts").json()["facts"]
    assert len(facts) == 1                       # le doublon est exclu
    assert facts[0]["subject_canonical"] == "Chat"
    assert client.get("/").status_code == 200

def test_get_facts_unknown_document_returns_404(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    import importlib, nerve.api as api
    importlib.reload(api)
    client = TestClient(api.app)
    assert client.get("/api/documents/9999/facts").status_code == 404

async def fake_transcode(cfg, url, *, client=None):
    return ("# Page\n\ncorps de la page", "Page")

def test_create_document_from_url(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")
    monkeypatch.setattr(pipe, "stream_chat", fake_stream)
    monkeypatch.setattr(pipe, "embed", fake_embed)
    import importlib, nerve.api as api
    importlib.reload(api)
    monkeypatch.setattr(api, "transcode_url", fake_transcode)
    client = TestClient(api.app)
    r = client.post("/api/documents", json={"url": "https://ex.com/a"})
    assert r.status_code == 200
    doc_id = r.json()["document_id"]
    doc = client.get(f"/api/documents/{doc_id}/facts").json()["document"]
    assert doc["source_kind"] == "url"
    assert doc["source_ref"] == "https://ex.com/a"
    assert doc["title"] == "Page"                       # titre transcodé (body.title défaut)
    assert json.loads(doc["params_json"])["dedup_field"] == api.cfg.dedup_field  # M-2 corrigé

def test_create_document_requires_text_or_url(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    import importlib, nerve.api as api
    importlib.reload(api)
    client = TestClient(api.app)
    assert client.post("/api/documents", json={}).status_code == 400

def test_upload_zip(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_DIM", "2")
    monkeypatch.setattr(pipe, "stream_chat", fake_stream)
    monkeypatch.setattr(pipe, "embed", fake_embed)
    import importlib, nerve.api as api
    importlib.reload(api)
    client = TestClient(api.app)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.txt", "le chat dort")
        z.writestr("bad.txt", "")                   # vide -> skipped
    r = client.post("/api/documents/upload",
                    files={"file": ("c.zip", buf.getvalue(), "application/zip")},
                    data={"set_name": "S"})
    assert r.status_code == 200
    body = r.json()
    assert body["skipped"] == ["bad.txt"]
    assert body["total_facts"] == 2                 # fake_stream émet 2 faits (1 doublon)
    doc = client.get(f"/api/documents/{body['document_id']}/facts").json()["document"]
    assert doc["source_kind"] == "file"
    assert doc["source_ref"] == "c.zip"

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
