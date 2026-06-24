# tests/test_vecutil.py
from nerve.vecutil import dot

def test_dot_est_le_produit_scalaire():
    # produit scalaire = somme des produits terme à terme
    assert dot([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]) == 32.0

def test_dot_de_vecteur_normalise_avec_lui_meme_vaut_un():
    # raison d'être de dot : sur des vecteurs L2-normalisés le produit scalaire
    # EST le cosinus, mesure de similarité des gardes fusion (entities) / dedup.
    v = [0.6, 0.8]   # norme 1
    assert abs(dot(v, v) - 1.0) < 1e-12
