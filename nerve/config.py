# nerve/config.py
import os
from dataclasses import dataclass

@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    api_key: str
    model: str

@dataclass(frozen=True)
class Config:
    llm: ProviderConfig
    embed: ProviderConfig
    embed_dim: int
    data_dir: str
    db_path: str
    port: int
    entity_threshold: float
    dedup_threshold: float
    dedup_field: str
    url_transcoders: tuple[str, ...]
    puremd_token: str
    jina_key: str

def load_config() -> Config:
    data_dir = os.environ.get("NERVE_DATA_DIR", "data")
    llm = ProviderConfig(
        base_url=os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1"),
        api_key=os.environ.get("LLM_API_KEY", "ollama"),
        model=os.environ.get("LLM_MODEL", "mistral-small3.2:24b-instruct-2506-q8_0"),
    )
    embed = ProviderConfig(
        base_url=os.environ.get("EMBED_BASE_URL", "http://localhost:11434/v1"),
        api_key=os.environ.get("EMBED_API_KEY", "ollama"),
        model=os.environ.get("EMBED_MODEL", "bge-m3"),
    )
    return Config(
        llm=llm, embed=embed,
        embed_dim=int(os.environ.get("EMBED_DIM", "1024")),
        data_dir=data_dir,
        db_path=os.path.join(data_dir, "nerve.db"),
        port=int(os.environ.get("NERVE_PORT", "3000")),
        entity_threshold=float(os.environ.get("ENTITY_THRESHOLD", "0.75")),
        dedup_threshold=float(os.environ.get("DEDUP_THRESHOLD", "0.85")),
        dedup_field=os.environ.get("DEDUP_FIELD", "triple"),
        url_transcoders=tuple(
            s.strip() for s in os.environ.get("URL_TRANSCODERS", "trafilatura").split(",")
            if s.strip()
        ),
        puremd_token=os.environ.get("PUREMD_API_TOKEN", ""),
        jina_key=os.environ.get("JINA_API_KEY", ""),
    )
