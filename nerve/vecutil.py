# nerve/vecutil.py
def dot(a: list[float], b: list[float]) -> float:
    """Produit scalaire. Sur des vecteurs L2-normalisés, vaut le cosinus."""
    return sum(x * y for x, y in zip(a, b))
