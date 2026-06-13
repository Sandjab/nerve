# tests/test_llm.py
import httpx
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
