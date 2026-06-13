# nerve/api.py
import os
import json
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Form, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from nerve.config import load_config
from nerve.store import Store
from nerve.scheduler import Scheduler, write_segments
from nerve.transcode import transcode_url
from nerve.ingest import ingest_upload, IngestError

cfg = load_config()
store = Store(cfg.db_path, embed_dim=cfg.embed_dim)
store.init_db()
scheduler = Scheduler(cfg, store)

@asynccontextmanager
async def lifespan(app):
    scheduler.start()
    scheduler.reconcile()
    yield
    await scheduler.stop()

app = FastAPI(title="nerve", lifespan=lifespan)
WEB = os.path.join(os.path.dirname(__file__), "web")

class CreateDoc(BaseModel):
    title: str = "Sans titre"
    text: str = ""
    url: str | None = None
    set_id: int | None = None
    set_name: str = "Défaut"

@app.post("/api/documents")
async def create_document(body: CreateDoc):
    set_id = body.set_id or store.create_set(body.set_name)
    if body.url:
        try:
            md, transcoded_title = await transcode_url(cfg, body.url)
        except RuntimeError as e:
            raise HTTPException(status_code=422, detail=str(e))
        title = body.title if body.title != "Sans titre" else (transcoded_title or body.url)
        doc_id = store.create_document(set_id, title, "url", source_ref=body.url,
                                       params={"dedup_field": cfg.dedup_field})
        segments = [(md, "")]
    elif body.text:
        doc_id = store.create_document(set_id, body.title, "text",
                                       params={"dedup_field": cfg.dedup_field})
        segments = [(body.text, "")]
    else:
        raise HTTPException(status_code=400, detail="text ou url requis")
    write_segments(cfg.data_dir, doc_id, segments)
    scheduler.enqueue(doc_id)
    return {"document_id": doc_id, "status": "queued"}

@app.post("/api/documents/upload")
async def upload_document(file: UploadFile = File(...), set_id: int | None = Form(None),
                          set_name: str = Form("Défaut"), title: str = Form("")):
    raw = await file.read()
    name = file.filename or "upload"
    sid = set_id or store.create_set(set_name)
    doc_id = store.create_document(sid, title or name, "file", source_ref=name,
                                   params={"dedup_field": cfg.dedup_field})
    dest = os.path.join(cfg.data_dir, "inputs", str(doc_id))
    try:
        segments, skipped = ingest_upload(name, raw, dest)
    except IngestError as e:
        store.finish_document(doc_id, error=str(e))
        raise HTTPException(status_code=422, detail=str(e))
    write_segments(cfg.data_dir, doc_id, segments)
    scheduler.enqueue(doc_id)
    return {"document_id": doc_id, "status": "queued", "skipped": skipped}

@app.get("/api/documents/{doc_id}")
def get_document(doc_id: int):
    doc = store.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document introuvable")
    return doc

@app.post("/api/documents/{doc_id}/pause")
def pause_document(doc_id: int):
    doc = scheduler.pause(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document introuvable")
    return doc

@app.post("/api/documents/{doc_id}/resume")
def resume_document(doc_id: int):
    if store.get_document(doc_id) is None:
        raise HTTPException(status_code=404, detail="Document introuvable")
    return scheduler.resume(doc_id)

@app.get("/api/documents/{doc_id}/events")
async def document_events(doc_id: int):
    if store.get_document(doc_id) is None:
        raise HTTPException(status_code=404, detail="Document introuvable")

    async def gen():
        q = scheduler.subscribe(doc_id)
        try:
            yield f"data: {json.dumps({'type': 'replay', 'facts': store.get_facts(doc_id)})}\n\n"
            doc = store.get_document(doc_id)
            yield f"data: {json.dumps({'type': 'status', 'status': doc['status'], 'total_facts': doc['total_facts'], 'unique_facts': doc['unique_facts'], 'duplicate_facts': doc['duplicate_facts']})}\n\n"
            if doc["status"] in ("done", "failed"):
                return
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev)}\n\n"
                    if ev.get("type") in ("done", "error"):
                        break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            scheduler.unsubscribe(doc_id, q)

    return StreamingResponse(gen(), media_type="text/event-stream")

@app.get("/api/documents/{doc_id}/facts")
def get_facts(doc_id: int):
    doc = store.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document introuvable")
    return {"document": doc, "facts": store.get_facts(doc_id)}

@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"))

def main():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=cfg.port)
