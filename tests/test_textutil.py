# tests/test_textutil.py
from nerve.textutil import chunk_text

def test_short_text_single_chunk():
    assert chunk_text("bonjour le monde") == ["bonjour le monde"]

def test_empty():
    assert chunk_text("   ") == []

def test_long_text_splits_without_loss():
    para = ("Phrase numéro un. " * 50).strip()
    text = "\n\n".join([para] * 40)            # ~ bien au-delà de la limite
    chunks = chunk_text(text, limit=2000)
    assert len(chunks) > 1
    assert all(len(c) <= 2000 for c in chunks)
    # aucun mot perdu : tous les mots du texte se retrouvent dans la concat
    original_words = set(text.split())
    joined_words = set(" ".join(chunks).split())
    assert original_words.issubset(joined_words)
