# tests/test_api.py
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
