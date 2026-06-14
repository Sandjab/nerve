# nerve/pipeline.py
from typing import AsyncGenerator
import httpx
from nerve.config import Config
from nerve.store import Store
from nerve.textutil import chunk_text
from nerve.extract import build_messages, FactStreamParser, FACT_RESPONSE_FORMAT
from nerve.llm import stream_chat
from nerve.embeddings import embed
from nerve.entities import EntityResolver
from nerve.dedup import FactDeduper

def _kind(raw) -> str:
    """Normalise le type d'un nœud : 'value' seulement si explicitement value, sinon entity."""
    return "value" if str(raw or "").strip().lower() == "value" else "entity"

async def run_extraction(cfg: Config, store: Store, doc_id: int,
                        segments: list[tuple[str, str]], *,
                        start_segment: int = 0, start_chunk: int = 0, client=None
                        ) -> AsyncGenerator[dict, None]:
    """segments = liste de (text, source_file). À la reprise (start_* > 0), le
    deduper/resolver sont pré-chargés depuis la DB et les chunks déjà traités
    (< (start_segment, start_chunk)) sont sautés."""
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=None)

    async def embed_one(s: str) -> list[float]:
        return (await embed(cfg.embed, [s], client=client))[0]

    resolver = EntityResolver(store, doc_id, embed_one, cfg.entity_threshold)
    deduper = FactDeduper(embed_one, cfg.dedup_threshold, field=cfg.dedup_field)
    if start_segment > 0 or start_chunk > 0:
        resolver.preload(store.load_entities(doc_id))
        deduper.preload(store.load_fact_vectors(doc_id))
    try:
        for si, (text, source_file) in enumerate(segments):
            for ci, chunk in enumerate(chunk_text(text)):
                if (si, ci) < (start_segment, start_chunk):
                    continue
                parser = FactStreamParser()
                msgs = build_messages(chunk)
                async for delta in stream_chat(
                    cfg.llm, msgs, client=client,
                    response_format=FACT_RESPONSE_FORMAT, temperature=0.7,
                ):
                    for fact in parser.feed(delta):
                        if not fact.get("subject") or not fact.get("object"):
                            continue
                        sid = await resolver.resolve(
                            fact["subject"], _kind(fact.get("subject_kind")))
                        oid = await resolver.resolve(
                            fact["object"], _kind(fact.get("object_kind")))
                        is_dup, dup_of, vec = await deduper.check(fact)
                        fid = store.add_fact(
                            doc_id, fact, is_duplicate=is_dup, dup_of_id=dup_of,
                            subject_entity_id=sid, object_entity_id=oid,
                            source_file=source_file)
                        if not is_dup:
                            deduper.add(fid, vec)
                            store.add_fact_vector(fid, vec)
                        yield {"type": "fact", "fact": {**fact, "id": fid},
                               "is_duplicate": is_dup, "source_file": source_file}
                yield {"type": "round_end", "segment": si, "chunk": ci,
                       "source_file": source_file}
        store.finish_document(doc_id)
        doc = store.get_document(doc_id)
        yield {"type": "done", "total_facts": doc["total_facts"],
               "unique_facts": doc["unique_facts"],
               "duplicate_facts": doc["duplicate_facts"]}
    except Exception as e:  # fail loud (embeddings KO, etc.)
        store.finish_document(doc_id, error=str(e))
        yield {"type": "error", "message": str(e)}
        raise
    finally:
        if owns_client:
            await client.aclose()
