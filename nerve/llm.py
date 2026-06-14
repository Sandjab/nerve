# nerve/llm.py
import json
from typing import AsyncGenerator
import httpx
from nerve.config import ProviderConfig

async def stream_chat(
    cfg: ProviderConfig,
    messages: list[dict],
    *,
    client: httpx.AsyncClient | None = None,
    response_format: dict | None = None,
    **params,
) -> AsyncGenerator[str, None]:
    """Stream les deltas de contenu d'un endpoint /chat/completions
    OpenAI-compatible (Ollama, OpenRouter, OpenAI...)."""
    payload = {"model": cfg.model, "messages": messages, "stream": True, **params}
    if response_format:
        payload["response_format"] = response_format
    headers = {"Authorization": f"Bearer {cfg.api_key}"}
    url = f"{cfg.base_url.rstrip('/')}/chat/completions"
    owns = client is None
    client = client or httpx.AsyncClient(timeout=None)
    try:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if "error" in obj:  # certains providers renvoient 200 + cadre d'erreur
                    err = obj["error"]
                    msg = err.get("message") if isinstance(err, dict) else str(err)
                    raise RuntimeError(msg or str(err))
                choices = obj.get("choices") or [{}]
                delta = (choices[0].get("delta") or {}).get("content")
                if delta:
                    yield delta
    finally:
        if owns:
            await client.aclose()

async def list_models(
    cfg: ProviderConfig,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """Liste les ids de modèles d'un endpoint /models OpenAI-compatible
    (Ollama, OpenRouter, OpenAI...). Fail-loud : lève sur statut non-2xx."""
    headers = {"Authorization": f"Bearer {cfg.api_key}"}
    url = f"{cfg.base_url.rstrip('/')}/models"
    owns = client is None
    client = client or httpx.AsyncClient(timeout=30)
    try:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json().get("data") or []
        return [m["id"] for m in data if m.get("id")]
    finally:
        if owns:
            await client.aclose()
