# nerve/extract.py
import json

FACT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "subject": {"type": "string"},
        "predicate": {"type": "string"},
        "object": {"type": "string"},
        "evidence_span": {"type": "string"},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["subject", "predicate", "object"],
}

# Sortie structurée OpenAI-compatible. Support variable selon provider :
# le parseur ci-dessous reste le filet si le provider ne l'honore pas.
FACT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "facts",
        "strict": False,
        "schema": {"type": "array", "items": FACT_SCHEMA},
    },
}

SYSTEM_PROMPT = (
    "Tu extrais des faits atomiques d'un document sous forme de triplets "
    "(subject, predicate, object). Le subject et l'object sont des entités ou "
    "valeurs canoniques et courtes (pas des phrases) pour que les nœuds se "
    "connectent. predicate est une relation précise en snake_case (<=32 car.). "
    "Pour chaque fait, ajoute title, description, evidence_span (citation "
    "verbatim), confidence (0-100) et tags. Réponds UNIQUEMENT par un tableau "
    "JSON d'objets, sans texte autour."
)

def build_messages(text: str, extra: str = "") -> list[dict]:
    user = (extra + "\n\n" if extra else "") + "Document :\n\n" + text
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]

class FactStreamParser:
    """Extrait les objets JSON {..} équilibrés d'un flux de texte, au fil de
    l'eau. Ignore le tableau englobant et le texte hors objets."""

    def __init__(self) -> None:
        self.buf = ""
        self.pos = 0

    def feed(self, text: str) -> list[dict]:
        self.buf += text
        out: list[dict] = []
        i = self.pos
        depth = 0
        start = None
        instr = False
        esc = False
        while i < len(self.buf):
            c = self.buf[i]
            if instr:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    instr = False
            else:
                if c == '"':
                    instr = True
                elif c == "{":
                    if depth == 0:
                        start = i
                    depth += 1
                elif c == "}":
                    if depth > 0:
                        depth -= 1
                        if depth == 0 and start is not None:
                            try:
                                out.append(json.loads(self.buf[start:i + 1]))
                            except json.JSONDecodeError:
                                pass
                            self.pos = i + 1
                            start = None
            i += 1
        return out
