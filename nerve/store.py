# nerve/store.py
import os
import json
from nerve.kinds import DEFAULT_KIND, winner
import sqlite3
import sqlite_vec
import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_sets (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  set_id INTEGER REFERENCES source_sets(id),
  title TEXT, source_kind TEXT, source_ref TEXT,
  status TEXT DEFAULT 'running', params_json TEXT,
  total_facts INTEGER DEFAULT 0,
  unique_facts INTEGER DEFAULT 0,
  duplicate_facts INTEGER DEFAULT 0,
  progress_segment INTEGER DEFAULT 0,
  progress_chunk INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now')), finished_at TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY,
  document_id INTEGER REFERENCES documents(id),
  subject TEXT, predicate TEXT, object TEXT,
  title TEXT, description TEXT, evidence_span TEXT,
  confidence INTEGER, tags_json TEXT, source_file TEXT,
  is_duplicate INTEGER DEFAULT 0, dup_of_id INTEGER,
  subject_entity_id INTEGER, object_entity_id INTEGER,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS entities (
  id INTEGER PRIMARY KEY,
  document_id INTEGER REFERENCES documents(id),
  canonical_name TEXT NOT NULL, normalized_key TEXT NOT NULL,
  mention_count INTEGER DEFAULT 1,
  kind TEXT DEFAULT 'concept',
  kind_votes TEXT DEFAULT '{}',
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_facts_doc ON facts(document_id);
CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject_entity_id);
CREATE INDEX IF NOT EXISTS idx_facts_object ON facts(object_entity_id);
CREATE INDEX IF NOT EXISTS idx_entities_doc_key ON entities(document_id, normalized_key);
"""

class Store:
    def __init__(self, db_path: str, embed_dim: int = 1024):
        self.db_path = db_path
        self.embed_dim = embed_dim
        self.conn: sqlite3.Connection | None = None

    def init_db(self) -> None:
        d = os.path.dirname(self.db_path)
        if d:
            os.makedirs(d, exist_ok=True)
        con = sqlite3.connect(self.db_path, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=5000")
        con.executescript(SCHEMA)
        con.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_facts "
            f"USING vec0(fact_id integer primary key, embedding float[{self.embed_dim}])"
        )
        con.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_entities "
            f"USING vec0(entity_id integer primary key, embedding float[{self.embed_dim}])"
        )
        con.commit()
        self.conn = con

    def create_set(self, name: str, description: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO source_sets(name, description) VALUES (?, ?)", (name, description))
        self.conn.commit()
        return cur.lastrowid

    def create_document(self, set_id: int, title: str, source_kind: str,
                        source_ref: str = "", params: dict | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO documents(set_id, title, source_kind, source_ref, params_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (set_id, title, source_kind, source_ref, json.dumps(params or {})))
        self.conn.commit()
        return cur.lastrowid

    def add_fact(self, document_id: int, fact: dict, *, is_duplicate: bool = False,
                 dup_of_id: int | None = None, subject_entity_id: int | None = None,
                 object_entity_id: int | None = None, source_file: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO facts(document_id, subject, predicate, object, title, "
            "description, evidence_span, confidence, tags_json, source_file, "
            "is_duplicate, dup_of_id, subject_entity_id, object_entity_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (document_id, fact.get("subject"), fact.get("predicate"),
             fact.get("object"), fact.get("title"), fact.get("description"),
             fact.get("evidence_span"), fact.get("confidence"),
             json.dumps(fact.get("tags", [])), source_file,
             1 if is_duplicate else 0, dup_of_id,
             subject_entity_id, object_entity_id))
        if is_duplicate:
            self.conn.execute(
                "UPDATE documents SET total_facts = total_facts + 1, "
                "duplicate_facts = duplicate_facts + 1 WHERE id = ?", (document_id,))
        else:
            self.conn.execute(
                "UPDATE documents SET total_facts = total_facts + 1, "
                "unique_facts = unique_facts + 1 WHERE id = ?", (document_id,))
        self.conn.commit()
        return cur.lastrowid

    def get_facts(self, document_id: int, include_duplicates: bool = False) -> list[dict]:
        where = "" if include_duplicates else " AND f.is_duplicate = 0"
        rows = self.conn.execute(
            # Colonnes listées explicitement (cf. #9) : les PK internes
            # subject_entity_id / object_entity_id ne sont pas exposées au client.
            "SELECT f.id, f.document_id, f.subject, f.predicate, f.object, "
            "f.title, f.description, f.evidence_span, f.confidence, f.tags_json, "
            "f.source_file, f.is_duplicate, f.dup_of_id, f.created_at, "
            "se.canonical_name AS subject_canonical, "
            "oe.canonical_name AS object_canonical FROM facts f "
            "LEFT JOIN entities se ON se.id = f.subject_entity_id "
            "LEFT JOIN entities oe ON oe.id = f.object_entity_id "
            "WHERE f.document_id = ?" + where + " ORDER BY f.id",
            (document_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["tags"] = json.loads(d.pop("tags_json") or "[]")
            d["subject_canonical"] = d.get("subject_canonical") or d["subject"]
            d["object_canonical"] = d.get("object_canonical") or d["object"]
            out.append(d)
        return out

    def get_document(self, document_id: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM documents WHERE id = ?",
                              (document_id,)).fetchone()
        return dict(r) if r else None

    def finish_document(self, document_id: int, error: str = "") -> None:
        self.conn.execute(
            "UPDATE documents SET status = ?, finished_at = datetime('now'), error = ? "
            "WHERE id = ?",
            ("failed" if error else "done", error or None, document_id))
        self.conn.commit()

    def set_status(self, document_id: int, status: str) -> None:
        self.conn.execute("UPDATE documents SET status = ? WHERE id = ?",
                          (status, document_id))
        self.conn.commit()

    def set_progress(self, document_id: int, segment: int, chunk: int) -> None:
        self.conn.execute(
            "UPDATE documents SET progress_segment = ?, progress_chunk = ? WHERE id = ?",
            (segment, chunk, document_id))
        self.conn.commit()

    def list_resumable(self) -> list[int]:
        rows = self.conn.execute(
            "SELECT id FROM documents WHERE status IN ('running','queued') ORDER BY id"
        ).fetchall()
        return [r["id"] for r in rows]

    def create_entity(self, document_id: int, canonical_name: str,
                      normalized_key: str, kind: str = DEFAULT_KIND) -> int:
        cur = self.conn.execute(
            "INSERT INTO entities(document_id, canonical_name, normalized_key, kind, kind_votes) "
            "VALUES (?, ?, ?, ?, ?)",
            (document_id, canonical_name, normalized_key, kind, json.dumps({kind: 1})))
        self.conn.commit()
        return cur.lastrowid

    def vote_entity_kind(self, entity_id: int, categorie: str) -> None:
        """Ajoute une voix pour `categorie` et recalcule kind = catégorie majoritaire
        (tie-break par ordre de la taxonomie). Fail-loud si l'état est illisible."""
        row = self.conn.execute(
            "SELECT kind_votes FROM entities WHERE id = ?", (entity_id,)).fetchone()
        if row is None:                                # fail-loud : entité inexistante = invariant violé
            raise ValueError(f"vote_entity_kind : entité {entity_id} introuvable")
        votes = json.loads(row["kind_votes"])          # lève si illisible (fail-loud)
        votes[categorie] = votes.get(categorie, 0) + 1
        self.conn.execute(
            "UPDATE entities SET kind = ?, kind_votes = ? WHERE id = ?",
            (winner(votes), json.dumps(votes), entity_id))
        self.conn.commit()

    def find_entity_by_key(self, document_id: int, normalized_key: str) -> int | None:
        r = self.conn.execute(
            "SELECT id FROM entities WHERE document_id = ? AND normalized_key = ?",
            (document_id, normalized_key)).fetchone()
        return r["id"] if r else None

    def set_entity_canonical(self, entity_id: int, canonical_name: str) -> None:
        self.conn.execute("UPDATE entities SET canonical_name = ? WHERE id = ?",
                          (canonical_name, entity_id))
        self.conn.commit()

    def bump_entity_mention(self, entity_id: int) -> None:
        self.conn.execute(
            "UPDATE entities SET mention_count = mention_count + 1 WHERE id = ?",
            (entity_id,))
        self.conn.commit()

    def add_entity_vector(self, entity_id: int, embedding: list[float]) -> None:
        self.conn.execute(
            "INSERT INTO vec_entities(entity_id, embedding) VALUES (?, ?)",
            (entity_id, sqlite_vec.serialize_float32(embedding)))
        self.conn.commit()

    def add_fact_vector(self, fact_id: int, embedding: list[float]) -> None:
        self.conn.execute(
            "INSERT INTO vec_facts(fact_id, embedding) VALUES (?, ?)",
            (fact_id, sqlite_vec.serialize_float32(embedding)))
        self.conn.commit()

    def load_fact_vectors(self, document_id: int) -> list[tuple[int, list[float]]]:
        """(fact_id, vecteur) des faits NON-dup du doc, depuis vec_facts."""
        rows = self.conn.execute(
            "SELECT v.fact_id AS fid, v.embedding AS emb FROM vec_facts v "
            "JOIN facts f ON f.id = v.fact_id "
            "WHERE f.document_id = ? AND f.is_duplicate = 0", (document_id,)).fetchall()
        return [(r["fid"], np.frombuffer(r["emb"], dtype=np.float32).tolist()) for r in rows]

    def load_entities(self, document_id: int) -> list[tuple[int, str, str, int, list[float]]]:
        """(id, canonical_name, normalized_key, mention_count, vecteur) du doc."""
        rows = self.conn.execute(
            "SELECT e.id AS id, e.canonical_name AS cn, e.normalized_key AS nk, "
            "e.mention_count AS mc, v.embedding AS emb FROM entities e "
            "JOIN vec_entities v ON v.entity_id = e.id "
            "WHERE e.document_id = ?", (document_id,)).fetchall()
        return [(r["id"], r["cn"], r["nk"], r["mc"],
                 np.frombuffer(r["emb"], dtype=np.float32).tolist()) for r in rows]

    def list_sets(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT s.id AS id, s.name AS name, s.description AS description, "
            "COUNT(d.id) AS document_count FROM source_sets s "
            "LEFT JOIN documents d ON d.set_id = s.id "
            "GROUP BY s.id, s.name, s.description ORDER BY s.id").fetchall()
        return [dict(r) for r in rows]

    _GRAPH_COLS = (
        "f.id AS fact_id, f.predicate AS predicate, f.confidence AS confidence, "
        "f.document_id AS document_id, d.set_id AS set_id, "
        "se.id AS s_entity_id, oe.id AS o_entity_id, "
        "se.kind_votes AS s_votes, oe.kind_votes AS o_votes, "
        "se.normalized_key AS s_key, se.canonical_name AS s_name, "
        "se.mention_count AS s_mentions, se.kind AS s_kind, "
        "oe.normalized_key AS o_key, oe.canonical_name AS o_name, "
        "oe.mention_count AS o_mentions, oe.kind AS o_kind")

    def facts_for_set(self, set_id: int, min_conf: int | None = None) -> list[dict]:
        sql = ("SELECT " + self._GRAPH_COLS + " FROM facts f "
               "JOIN documents d ON d.id = f.document_id "
               "JOIN entities se ON se.id = f.subject_entity_id "
               "JOIN entities oe ON oe.id = f.object_entity_id "
               "WHERE d.set_id = ? AND f.is_duplicate = 0")
        params: list = [set_id]
        if min_conf is not None:
            sql += " AND f.confidence >= ?"
            params.append(min_conf)
        sql += " ORDER BY f.id"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def search_facts(self, query_vec: list[float], k: int,
                     sets: list[int] | None = None) -> list[dict]:
        knn_k = k if not sets else max(k * 10, 100)
        rows = self.conn.execute(
            "SELECT v.fact_id AS fact_id, v.distance AS distance, "
            "f.subject AS subject, f.predicate AS predicate, f.object AS object, "
            "f.description AS description, f.document_id AS document_id, "
            "d.set_id AS set_id FROM vec_facts v "
            "JOIN facts f ON f.id = v.fact_id "
            "JOIN documents d ON d.id = f.document_id "
            "WHERE v.embedding MATCH ? AND k = ?",
            (sqlite_vec.serialize_float32(query_vec), knn_k)).fetchall()
        out: list[dict] = []
        for r in rows:
            if sets and r["set_id"] not in sets:
                continue
            out.append(dict(r))
            if len(out) >= k:
                break
        return out

    def entities_by_key(self, normalized_key: str,
                        sets: list[int] | None = None) -> list[dict]:
        sql = ("SELECT e.id AS id, e.normalized_key AS normalized_key, "
               "e.canonical_name AS canonical_name, e.document_id AS document_id, "
               "d.set_id AS set_id FROM entities e "
               "JOIN documents d ON d.id = e.document_id WHERE e.normalized_key = ?")
        params: list = [normalized_key]
        if sets:
            sql += " AND d.set_id IN (%s)" % ",".join("?" * len(sets))
            params += list(sets)
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def entity_neighbors(self, query_vec: list[float], k: int,
                         sets: list[int] | None = None) -> list[dict]:
        knn_k = k if not sets else max(k * 10, 100)
        rows = self.conn.execute(
            "SELECT v.entity_id AS entity_id, v.distance AS distance, "
            "e.normalized_key AS normalized_key, e.canonical_name AS canonical_name, "
            "d.set_id AS set_id FROM vec_entities v "
            "JOIN entities e ON e.id = v.entity_id "
            "JOIN documents d ON d.id = e.document_id "
            "WHERE v.embedding MATCH ? AND k = ?",
            (sqlite_vec.serialize_float32(query_vec), knn_k)).fetchall()
        out: list[dict] = []
        for r in rows:
            if sets and r["set_id"] not in sets:
                continue
            out.append(dict(r))
            if len(out) >= k:
                break
        return out

    def facts_for_entities(self, entity_ids: list[int],
                           min_conf: int | None = None) -> list[dict]:
        if not entity_ids:
            return []
        ph = ",".join("?" * len(entity_ids))
        sql = ("SELECT " + self._GRAPH_COLS + " FROM facts f "
               "JOIN documents d ON d.id = f.document_id "
               "JOIN entities se ON se.id = f.subject_entity_id "
               "JOIN entities oe ON oe.id = f.object_entity_id "
               "WHERE f.is_duplicate = 0 AND "
               f"(f.subject_entity_id IN ({ph}) OR f.object_entity_id IN ({ph}))")
        params: list = list(entity_ids) + list(entity_ids)
        if min_conf is not None:
            sql += " AND f.confidence >= ?"
            params.append(min_conf)
        sql += " ORDER BY f.id"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_set(self, set_id: int) -> dict | None:
        s = self.conn.execute(
            "SELECT * FROM source_sets WHERE id = ?", (set_id,)).fetchone()
        if s is None:
            return None
        docs = self.conn.execute(
            "SELECT id, title, source_kind, source_ref, status, total_facts, "
            "unique_facts, duplicate_facts, created_at FROM documents "
            "WHERE set_id = ? ORDER BY id", (set_id,)).fetchall()
        out = dict(s)
        out["documents"] = [dict(r) for r in docs]
        return out
