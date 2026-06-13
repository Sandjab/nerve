# tests/test_embeddings.py
import math
import httpx
from nerve.config import ProviderConfig
from nerve.embeddings import embed

async def test_embed_returns_normalized_vectors():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/embeddings")
        body = request.read().decode()
        assert "deux textes" in body or "un texte" in body
        return httpx.Response(200, json={"data": [
            {"embedding": [3.0, 4.0]},     # norme 5 -> (0.6, 0.8)
            {"embedding": [0.0, 2.0]},     # norme 2 -> (0.0, 1.0)
        ]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = ProviderConfig("http://x/v1", "k", "m")
    out = await embed(cfg, ["un texte", "deux textes"], client=client)
    assert len(out) == 2
    assert abs(out[0][0] - 0.6) < 1e-6 and abs(out[0][1] - 0.8) < 1e-6
    assert abs(math.sqrt(sum(v * v for v in out[1])) - 1.0) < 1e-6
