# nerve/embeddings.py
import math
import httpx
from nerve.config import ProviderConfig

def _l2_normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]

async def embed(cfg: ProviderConfig, texts: list[str], *,
                client: httpx.AsyncClient | None = None) -> list[list[float]]:
    """Embeddings via un endpoint /embeddings OpenAI-compatible (Ollama bge-m3...).
    Renvoie des vecteurs L2-normalisés (cosinus = produit scalaire)."""
    payload = {"model": cfg.model, "input": texts}
    headers = {"Authorization": f"Bearer {cfg.api_key}"}
    url = f"{cfg.base_url.rstrip('/')}/embeddings"
    owns = client is None
    client = client or httpx.AsyncClient(timeout=None)
    try:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return [_l2_normalize(row["embedding"]) for row in data["data"]]
    finally:
        if owns:
            await client.aclose()
