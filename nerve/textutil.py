# nerve/textutil.py
def chunk_text(text: str, limit: int = 24000) -> list[str]:
    """Découpe le texte en morceaux <= limit, sans troncature.
    Recule vers une frontière de paragraphe, puis phrase, puis mot."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if n - i <= limit:
            tail = text[i:].strip()
            if tail:
                chunks.append(tail)
            break
        window = text[i:i + limit]
        floor = int(limit * 0.5)
        cut = window.rfind("\n\n")
        if cut < floor:
            cut = window.rfind("\n")
        if cut < floor:
            cut = window.rfind(". ")
            if cut != -1:
                cut += 1  # garder le point
        if cut < floor:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit
        piece = text[i:i + cut].strip()
        if piece:
            chunks.append(piece)
        i += cut
    return chunks
