# tests/test_config.py
import importlib
from nerve import config as cfgmod

def test_defaults(monkeypatch):
    for k in ("LLM_BASE_URL","LLM_MODEL","EMBED_MODEL","EMBED_DIM","NERVE_DATA_DIR"):
        monkeypatch.delenv(k, raising=False)
    c = cfgmod.load_config()
    assert c.llm.base_url == "http://localhost:11434/v1"
    assert c.llm.model == "qwen3.6"
    assert c.embed.model == "bge-m3"
    assert c.embed_dim == 1024
    assert c.db_path.endswith("nerve.db")

def test_env_override(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("LLM_MODEL", "anthropic/claude-3.5")
    monkeypatch.setenv("EMBED_DIM", "768")
    c = cfgmod.load_config()
    assert c.llm.base_url == "https://openrouter.ai/api/v1"
    assert c.llm.model == "anthropic/claude-3.5"
    assert c.embed_dim == 768
