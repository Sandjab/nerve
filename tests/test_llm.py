# tests/test_llm.py
import httpx
import pytest
from nerve.config import ProviderConfig
from nerve.llm import stream_chat

async def test_stream_yields_content_deltas():
    body = (
        'data: {"choices":[{"delta":{"content":"Bon"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"jour"}}]}\n\n'
        'data: [DONE]\n\n'
    )
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(200, text=body)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = ProviderConfig("http://x/v1", "k", "m")
    out = []
    async for delta in stream_chat(cfg, [{"role": "user", "content": "salut"}], client=client):
        out.append(delta)
    assert "".join(out) == "Bonjour"

async def test_stream_raises_on_error_frame():
    # Un provider (OpenRouter, OpenAI...) peut renvoyer HTTP 200 avec un cadre
    # d'erreur dans le flux : il ne faut PAS finir silencieusement à 0 fait.
    body = 'data: {"error":{"message":"model not found"}}\n\n'
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = ProviderConfig("http://x/v1", "k", "m")
    with pytest.raises(RuntimeError, match="model not found"):
        async for _ in stream_chat(cfg, [{"role": "user", "content": "x"}], client=client):
            pass

from nerve.llm import list_models

async def test_list_models_returns_ids():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"data": [{"id": "qwen3.6"}, {"id": "bge-m3"}]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = ProviderConfig("http://x/v1", "k", "m")
    assert await list_models(cfg, client=client) == ["qwen3.6", "bge-m3"]

async def test_list_models_raises_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = ProviderConfig("http://x/v1", "k", "m")
    with pytest.raises(httpx.HTTPStatusError):
        await list_models(cfg, client=client)
