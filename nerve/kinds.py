# nerve/kinds.py
"""Taxonomie des types de nœud (issue #11). Source unique pour store / pipeline / graph."""

KINDS = ["personne", "lieu", "organisation", "concept", "date", "quantite"]
DEFAULT_KIND = "concept"   # repli pour l'abstrait / l'ambigu / l'inconnu

def normalize_kind(raw) -> str:
    """Ramène une étiquette LLM à une catégorie valide ; repli DEFAULT_KIND sinon."""
    k = str(raw or "").strip().lower()
    return k if k in KINDS else DEFAULT_KIND

def winner(votes: dict) -> str:
    """Catégorie majoritaire d'un Counter de votes ; à égalité, l'ordre de KINDS
    tranche (indice le plus petit). Counter vide -> DEFAULT_KIND. Suppose des clés
    valides (cf. normalize_kind à l'entrée du vote)."""
    if not votes:
        return DEFAULT_KIND
    return min(votes, key=lambda k: (-votes[k], KINDS.index(k)))
