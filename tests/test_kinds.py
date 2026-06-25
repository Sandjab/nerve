# tests/test_kinds.py
from nerve.kinds import KINDS, DEFAULT_KIND, normalize_kind, winner

def test_taxonomie_ordonnee_a_six_categories():
    assert KINDS == ["personne", "lieu", "organisation", "concept", "date", "quantite"]
    assert DEFAULT_KIND == "concept"

def test_normalize_kind_accepte_les_categories_et_replie_l_inconnu():
    assert normalize_kind("Personne") == "personne"
    assert normalize_kind("  DATE ") == "date"
    assert normalize_kind("entity") == "concept"   # ancien domaine -> repli
    assert normalize_kind(None) == "concept"

def test_winner_argmax_avec_tie_break_par_ordre():
    assert winner({"personne": 3, "concept": 1}) == "personne"
    # égalité 2-2 : l'ordre de KINDS tranche (lieu avant organisation)
    assert winner({"organisation": 2, "lieu": 2}) == "lieu"
    # concept (repli, 4e) perd l'égalité face à une catégorie spécifique
    assert winner({"concept": 2, "quantite": 2}) == "concept"
    assert winner({}) == DEFAULT_KIND
