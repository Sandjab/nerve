# nerve/ingest.py
import os
import trafilatura
from pypdf import PdfReader
from docx import Document


class IngestError(Exception):
    """Lecture impossible (fichier vide, illisible, format cassé)."""


TEXT_EXTS = {".txt", ".md", ".markdown", ".text", ".rst", ".json", ".jsonl",
             ".csv", ".tsv", ".log", ".html", ".htm", ".pdf", ".docx",
             ".xml", ".yaml", ".yml", ".py", ".js", ".ts"}


def _read_pdf(path: str) -> str:
    try:
        reader = PdfReader(path)
        return "\n\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as e:
        raise IngestError(f"PDF illisible : {e}")


def _read_docx(path: str) -> str:
    try:
        doc = Document(path)
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                parts.append("\t".join(c.text for c in row.cells))
        return "\n".join(parts)
    except Exception as e:
        raise IngestError(f"docx illisible : {e}")


def _read_html(path: str) -> str:
    raw = open(path, encoding="utf-8", errors="ignore").read()
    return trafilatura.extract(raw, output_format="markdown") or ""


def read_file(path: str, name: str) -> str:
    """Extrait le texte d'un fichier. Lève IngestError si vide/illisible."""
    ext = os.path.splitext(name)[1].lower()
    if ext == ".pdf":
        text = _read_pdf(path)
    elif ext == ".docx":
        text = _read_docx(path)
    elif ext in (".html", ".htm"):
        text = _read_html(path)
    else:
        text = open(path, encoding="utf-8", errors="ignore").read()
    if not text or not text.strip():
        raise IngestError(f"Contenu vide/illisible : {name}")
    return text
