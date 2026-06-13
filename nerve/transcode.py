# nerve/transcode.py
import asyncio
import httpx
import trafilatura
from nerve.config import Config


def _first_title(md: str) -> str:
    """Titre = 1re ligne « Title: … » ou « # … » dans les 5 premières lignes."""
    for line in md.strip().split("\n")[:5]:
        if line.startswith("Title:"):
            return line[len("Title:"):].strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""


async def _trafilatura(url: str, cfg: Config, *, client) -> tuple[str, str] | None:
    """Backend local (défaut) : n'utilise pas `client` ni de clé. fetch_url/extract
    sont synchrones (urllib3) -> exécutés hors boucle via to_thread."""
    downloaded = await asyncio.to_thread(trafilatura.fetch_url, url)
    if not downloaded:
        return None
    md = await asyncio.to_thread(trafilatura.extract, downloaded, output_format="markdown")
    if not md or not md.strip():
        return None
    meta = trafilatura.extract_metadata(downloaded)
    title = (meta.title or "") if meta else ""
    return md, title


async def _jina(url: str, cfg: Config, *, client) -> tuple[str, str] | None:
    """Jina Reader (r.jina.ai). Activé si JINA_API_KEY est défini."""
    if not cfg.jina_key:
        return None
    r = await client.get(
        f"https://r.jina.ai/{url}",
        headers={"Authorization": f"Bearer {cfg.jina_key}", "Accept": "text/markdown"},
    )
    r.raise_for_status()
    text = r.text
    if not text.strip():
        return None
    return text, _first_title(text)


async def _puremd(url: str, cfg: Config, *, client) -> tuple[str, str] | None:
    """Pure.md (préfixe d'URL). Activé si PUREMD_API_TOKEN est défini."""
    if not cfg.puremd_token:
        return None
    r = await client.get(
        f"https://pure.md/{url}",
        headers={"x-puremd-api-token": cfg.puremd_token},
    )
    r.raise_for_status()
    text = r.text
    if not text.strip():
        return None
    return text, _first_title(text)
