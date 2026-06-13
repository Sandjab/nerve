import httpx
import nerve.transcode as tc
from nerve.config import load_config

def test_first_title_from_title_line():
    assert tc._first_title("Title: Mon Doc\n\n# autre\ncorps") == "Mon Doc"

def test_first_title_from_heading():
    assert tc._first_title("# Mon Titre\n\ncorps") == "Mon Titre"

def test_first_title_absent():
    assert tc._first_title("corps sans titre\nligne 2") == ""

async def test_trafilatura_backend(monkeypatch):
    monkeypatch.setattr(tc.trafilatura, "fetch_url", lambda u: "<html>x</html>")
    monkeypatch.setattr(tc.trafilatura, "extract", lambda html, **k: "# T\n\ncorps")
    class _M:
        title = "T"
    monkeypatch.setattr(tc.trafilatura, "extract_metadata", lambda html: _M())
    md, title = await tc._trafilatura("http://x", load_config(), client=None)
    assert md == "# T\n\ncorps"
    assert title == "T"

async def test_trafilatura_backend_empty_returns_none(monkeypatch):
    monkeypatch.setattr(tc.trafilatura, "fetch_url", lambda u: None)
    assert await tc._trafilatura("http://x", load_config(), client=None) is None

async def test_jina_backend(monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", "K")
    cfg = load_config()
    seen = {}
    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, text="Title: Foo\n\n# Foo\n\ncorps")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    md, title = await tc._jina("https://ex.com/a", cfg, client=client)
    await client.aclose()
    assert seen["url"].startswith("https://r.jina.ai/")
    assert "ex.com" in seen["url"]
    assert seen["auth"] == "Bearer K"
    assert title == "Foo"

async def test_jina_skips_without_key(monkeypatch):
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    assert await tc._jina("http://x", load_config(), client=None) is None

async def test_puremd_backend(monkeypatch):
    monkeypatch.setenv("PUREMD_API_TOKEN", "T")
    cfg = load_config()
    seen = {}
    def handler(request):
        seen["url"] = str(request.url)
        seen["tok"] = request.headers.get("x-puremd-api-token")
        return httpx.Response(200, text="# Bar\n\ncorps")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    md, title = await tc._puremd("https://ex.com/a", cfg, client=client)
    await client.aclose()
    assert seen["url"].startswith("https://pure.md/")
    assert seen["tok"] == "T"
    assert title == "Bar"

async def test_puremd_skips_without_key(monkeypatch):
    monkeypatch.delenv("PUREMD_API_TOKEN", raising=False)
    assert await tc._puremd("http://x", load_config(), client=None) is None
