# tests/test_extract.py
from nerve.extract import FactStreamParser, build_messages, FACT_RESPONSE_FORMAT

def test_parses_two_complete_objects():
    p = FactStreamParser()
    s = '[{"subject":"A","predicate":"r","object":"B"},'\
        '{"subject":"C","predicate":"s","object":"D"}]'
    facts = p.feed(s)
    assert [f["subject"] for f in facts] == ["A", "C"]

def test_yields_objects_incrementally():
    p = FactStreamParser()
    assert p.feed('[{"subject":"A","predicate":"r",') == []      # objet incomplet
    facts = p.feed('"object":"B"}]')
    assert len(facts) == 1 and facts[0]["object"] == "B"

def test_ignores_braces_inside_strings():
    p = FactStreamParser()
    facts = p.feed('[{"subject":"x {y}","predicate":"r","object":"z"}]')
    assert facts[0]["subject"] == "x {y}"

def test_build_messages_includes_text():
    msgs = build_messages("le chat dort")
    assert msgs[-1]["role"] == "user"
    assert "le chat dort" in msgs[-1]["content"]
    assert FACT_RESPONSE_FORMAT["type"] == "json_schema"

def test_system_prompt_porte_les_regles_canoniques():
    from nerve.extract import SYSTEM_PROMPT
    p = SYSTEM_PROMPT.lower()
    # règles clés : nœuds = entités canoniques, réutiliser la MÊME chaîne, pas de prose
    assert "canonique" in p
    assert "même chaîne" in p or "meme chaine" in p
    assert "description" in p

def test_fact_schema_has_kind_fields():
    from nerve.extract import FACT_SCHEMA
    props = FACT_SCHEMA["properties"]
    assert props["subject_kind"]["enum"] == ["entity", "value"]
    assert props["object_kind"]["enum"] == ["entity", "value"]
