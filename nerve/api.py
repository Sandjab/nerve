# nerve/api.py
import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
from nerve.config import load_config
from nerve.store import Store
from nerve.pipeline import run_extraction
from nerve.transcode import transcode_url
from fastapi import Form, UploadFile, File

cfg = load_config()
store = Store(cfg.db_path, embed_dim=cfg.embed_dim)
store.init_db()
app = FastAPI(title="nerve")
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
        md, transcoded_title = await transcode_url(cfg, body.url)
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
    async for _ in run_extraction(cfg, store, doc_id, segments):
        pass  # Plan 1/I-2 : on consomme jusqu'au bout (SSE live = I-3)
    doc = store.get_document(doc_id)
    return {"document_id": doc_id, "total_facts": doc["total_facts"],
            "unique_facts": doc["unique_facts"],
            "duplicate_facts": doc["duplicate_facts"],
            "status": doc["status"]}

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
