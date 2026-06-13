import pytest
import nerve.ingest as ing
from nerve.ingest import IngestError

def test_read_txt(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("contenu texte", encoding="utf-8")
    assert ing.read_file(str(p), "a.txt") == "contenu texte"

def test_read_empty_raises(tmp_path):
    p = tmp_path / "v.txt"
    p.write_text("   \n  ", encoding="utf-8")
    with pytest.raises(IngestError):
        ing.read_file(str(p), "v.txt")

def test_read_docx(tmp_path):
    from docx import Document
    d = Document()
    d.add_paragraph("Bonjour")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text = "A"
    t.rows[0].cells[1].text = "B"
    p = tmp_path / "doc.docx"
    d.save(str(p))
    out = ing.read_file(str(p), "doc.docx")
    assert "Bonjour" in out
    assert "A" in out and "B" in out

def test_read_pdf_via_pypdf(tmp_path, monkeypatch):
    class _Page:
        def extract_text(self):
            return "page un"
    class _Reader:
        def __init__(self, path):
            self.pages = [_Page()]
    monkeypatch.setattr(ing, "PdfReader", _Reader)
    p = tmp_path / "x.pdf"
    p.write_bytes(b"%PDF-1.4 factice")
    assert ing.read_file(str(p), "x.pdf") == "page un"

def test_read_pdf_corrupt_raises(tmp_path, monkeypatch):
    def _boom(path):
        raise ValueError("pdf cassé")
    monkeypatch.setattr(ing, "PdfReader", _boom)
    p = tmp_path / "bad.pdf"
    p.write_bytes(b"pas un pdf")
    with pytest.raises(IngestError):
        ing.read_file(str(p), "bad.pdf")

def test_read_html_via_trafilatura(tmp_path, monkeypatch):
    monkeypatch.setattr(ing.trafilatura, "extract", lambda raw, **k: "# T\n\ncorps")
    p = tmp_path / "page.html"
    p.write_text("<html><body><p>x</p></body></html>", encoding="utf-8")
    assert "corps" in ing.read_file(str(p), "page.html")
