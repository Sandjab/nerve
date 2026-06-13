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
