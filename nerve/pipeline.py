# nerve/pipeline.py
from typing import AsyncGenerator
from nerve.config import Config
from nerve.store import Store
from nerve.textutil import chunk_text
from nerve.extract import build_messages, FactStreamParser, FACT_RESPONSE_FORMAT
from nerve.llm import stream_chat

async def run_extraction(cfg: Config, store: Store, doc_id: int, text: str,
                        *, client=None) -> AsyncGenerator[dict, None]:
    try:
        for ci, chunk in enumerate(chunk_text(text)):
            parser = FactStreamParser()
            msgs = build_messages(chunk)
            async for delta in stream_chat(
                cfg.llm, msgs, client=client,
                response_format=FACT_RESPONSE_FORMAT, temperature=0.7,
            ):
                for fact in parser.feed(delta):
                    if not fact.get("subject") or not fact.get("object"):
                        continue
                    fid = store.add_fact(doc_id, fact)
                    yield {"type": "fact", "fact": {**fact, "id": fid}}
            yield {"type": "round_end", "chunk": ci}
        store.finish_document(doc_id)
        yield {"type": "done"}
    except Exception as e:  # remonter l'échec sans l'avaler
        store.finish_document(doc_id, error=str(e))
        yield {"type": "error", "message": str(e)}
        raise
