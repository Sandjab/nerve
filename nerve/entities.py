# nerve/entities.py
import re
import unicodedata
from collections import Counter

from nerve.vecutil import dot

def normalized_key(name: str) -> str:
    """Clé déterministe : sans accents, casefold, [_\\W]+ -> espace, espaces normalisés."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.casefold()
    s = re.sub(r"[_\W]+", " ", s)
    return " ".join(s.split())

def _acronym(key: str) -> str:
    return "".join(w[0] for w in key.split() if w)

def lexical_guard(key_a: str, key_b: str) -> bool:
    """Vrai si un lien lexical autorise la fusion : sous-chaîne, sous-ensemble de
    tokens (l'un inclus dans l'autre), ou acronyme. Le simple token partagé ne
    suffit PAS (sinon « Notker le Bègue » / « Notker le Chauve » fusionneraient)."""
    if not key_a or not key_b:
        return False
    if key_a in key_b or key_b in key_a:
        return True
    ta, tb = set(key_a.split()), set(key_b.split())
    if ta <= tb or tb <= ta:
        return True
    flat_a, flat_b = key_a.replace(" ", ""), key_b.replace(" ", "")
    return _acronym(key_a) == flat_b or _acronym(key_b) == flat_a

class EntityResolver:
    """Résolution d'entités intra-document : clé lexicale puis garde hybride
    embedding/lexical. Registre en mémoire + persistance store/vec_entities."""

    def __init__(self, store, document_id: int, embed_fn, threshold: float):
        self.store = store
        self.doc = document_id
        self.embed_fn = embed_fn          # async (text) -> list[float]
        self.threshold = threshold
        self._by_key: dict[str, int] = {}            # normalized_key -> entity_id
        self._entities: list[tuple[int, list[float], str]] = []  # (id, vec, key)
        self._surface: dict[int, Counter] = {}       # entity_id -> Counter(surface forms)

    def preload(self, rows: list[tuple[int, str, str, int, list[float]]]) -> None:
        """Reconstruit le registre à la reprise : (id, canonical, key, mention, vec)."""
        for eid, canonical, key, mention, vec in rows:
            self._by_key[key] = eid
            self._entities.append((eid, vec, key))
            self._surface[eid] = Counter({canonical: mention})

    def _note(self, eid: int, name: str) -> None:
        self._surface[eid][name] += 1
        # canonique = forme la plus fréquente (égalité -> la plus courte)
        best = sorted(self._surface[eid].items(), key=lambda kv: (-kv[1], len(kv[0])))[0][0]
        self.store.set_entity_canonical(eid, best)

    async def resolve(self, name: str, kind: str = "entity") -> int:
        key = normalized_key(name)
        if key in self._by_key:
            eid = self._by_key[key]
            self.store.bump_entity_mention(eid)
            if kind == "entity":
                self.store.promote_entity_kind(eid)
            self._note(eid, name)
            return eid
        vec = await self.embed_fn(name)
        eid = self._match(key, vec)
        if eid is None:
            eid = self.store.create_entity(self.doc, canonical_name=name,
                                           normalized_key=key, kind=kind)
            self.store.add_entity_vector(eid, vec)
            self._entities.append((eid, vec, key))
            self._surface[eid] = Counter()
        else:
            self.store.bump_entity_mention(eid)
            if kind == "entity":
                self.store.promote_entity_kind(eid)
        self._by_key[key] = eid
        self._note(eid, name)
        return eid

    def _match(self, key: str, vec: list[float]) -> int | None:
        best_eid, best_sim = None, 0.0
        for eid, evec, ekey in self._entities:
            sim = dot(vec, evec)
            if sim >= self.threshold and lexical_guard(key, ekey) and sim > best_sim:
                best_eid, best_sim = eid, sim
        return best_eid
