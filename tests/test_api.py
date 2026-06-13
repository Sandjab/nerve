# tests/test_api.py
import nerve.pipeline as pipe
from fastapi.testclient import TestClient

async def fake_stream(cfg, messages, **kw):
    yield '[{"subject":"Chat","predicate":"dort_sur","object":"Tapis"}]'

def test_create_document_and_get_facts(tmp_path, monkeypatch):
    monkeypatch.setenv("NERVE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(pipe, "stream_chat", fake_stream)
    import importlib, nerve.api as api
    importlib.reload(api)                       # relit NERVE_DATA_DIR
    client = TestClient(api.app)
    r = client.post("/api/documents", json={"title": "t", "text": "le chat dort"})
    assert r.status_code == 200
    doc_id = r.json()["document_id"]
    assert r.json()["total_facts"] == 1
    facts = client.get(f"/api/documents/{doc_id}/facts").json()["facts"]
    assert facts[0]["subject"] == "Chat"
    assert client.get("/").status_code == 200
