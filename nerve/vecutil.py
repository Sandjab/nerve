# nerve/vecutil.py
def dot(a: list[float], b: list[float]) -> float:
    """Produit scalaire. Sur des vecteurs L2-normalisés, vaut le cosinus.

    Fail-loud : refuse des dimensions différentes plutôt que de laisser zip
    tronquer silencieusement (cosinus faussé masquant un bug modèle/config)."""
    if len(a) != len(b):
        raise ValueError(f"dimensions incompatibles : {len(a)} != {len(b)}")
    return sum(x * y for x, y in zip(a, b))
