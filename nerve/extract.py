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
        "subject_kind": {"type": "string", "enum": ["entity", "value"]},
        "object_kind": {"type": "string", "enum": ["entity", "value"]},
        "evidence_span": {"type": "string"},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["subject", "predicate", "object"],
}

# Sortie structurée OpenAI-compatible. On exige TOUS les champs (schéma strict) :
# à schéma lâche (seuls subject/predicate/object requis), les modèles omettent
# subject_kind/object_kind/evidence_span de façon non déterministe (cf. Benchmark_LLM.md).
# Support variable selon provider : le parseur ci-dessous reste le filet si non honoré.
FACT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "facts",
        "strict": True,
        "schema": {"type": "array", "items": {
            **FACT_SCHEMA,
            "required": list(FACT_SCHEMA["properties"].keys()),
        }},
    },
}

SYSTEM_PROMPT = (
    "Tu extrais un graphe de connaissances d'un document. Réponds UNIQUEMENT par un "
    "tableau JSON d'objets (faits atomiques), sans texte autour. Un document dense "
    "justifie 8 à 15 faits ; une page courte 0 à 3.\n\n"
    "RÈGLE LA PLUS IMPORTANTE (elle pilote la connectivité du graphe) : subject et "
    "object DOIVENT être des ENTITÉS canoniques ou des VALEURS atomiques courtes, "
    "jamais de la prose. Ce sont des nœuds : la même entité doit sortir IDENTIQUE à "
    "chaque fois pour que les arêtes se connectent. Mets le récit, la preuve et la "
    "nuance dans description, PAS dans subject/object.\n\n"
    "Règles subject / object :\n"
    "- Utilise le nom canonique le plus court d'une entité réelle (personne, "
    "organisation, lieu, œuvre, méthode, date, nombre+unité, version).\n"
    "- Retire articles, rôles et qualificatifs : « l'équipe de Cluny » -> « Cluny ».\n"
    "- Réutilise EXACTEMENT la même chaîne pour la même entité dans tous les faits "
    "(c'est ainsi que les nœuds fusionnent). Pas de pronom ni de paraphrase.\n"
    "- Jamais de phrase ou de proposition dans subject/object. Si la valeur est "
    "descriptive, mets la valeur atomique dans object et explique dans description.\n"
    "- Privilégie les arêtes entité-entité (deux entités nommées) ; entité-valeur "
    "est correct aussi.\n\n"
    "Pour subject ET object, indique aussi son type via subject_kind / object_kind : "
    "« entity » = entité nommée (personne, lieu, organisation, œuvre, concept réifié) ; "
    "« value » = valeur littérale (date, nombre, mesure, durée, quantité, proportion). "
    "Ex. (Cluny, fonde, 910) -> subject_kind=entity, object_kind=value.\n\n"
    "Pour chaque fait, fournis TOUS ces champs : title (une phrase <=140 car.), "
    "description (2-3 phrases <=350 car. portant la réponse + preuve, citation "
    "verbatim si utile), subject, subject_kind (entity|value), predicate (relation "
    "snake_case précise, <=32 car.), object, object_kind (entity|value), evidence_span "
    "(citation verbatim, sous-chaîne du document), confidence (0-100), tags "
    "(minuscules, alphanumérique+tiret). N'omets JAMAIS subject_kind ni object_kind."
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
