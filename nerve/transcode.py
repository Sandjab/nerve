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
