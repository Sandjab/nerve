# nerve/store.py
import os
import json
import sqlite3
import sqlite_vec

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
  created_at TEXT DEFAULT (datetime('now')), finished_at TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY,
  document_id INTEGER REFERENCES documents(id),
  subject TEXT, predicate TEXT, object TEXT,
  title TEXT, description TEXT, evidence_span TEXT,
  confidence INTEGER, tags_json TEXT, source_file TEXT,
  is_duplicate INTEGER DEFAULT 0, dup_of_id INTEGER,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_facts_doc ON facts(document_id);
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
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
        con.executescript(SCHEMA)
        con.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_facts "
            f"USING vec0(fact_id integer primary key, embedding float[{self.embed_dim}])"
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

    def add_fact(self, document_id: int, fact: dict, source_file: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO facts(document_id, subject, predicate, object, title, "
            "description, evidence_span, confidence, tags_json, source_file) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (document_id, fact.get("subject"), fact.get("predicate"),
             fact.get("object"), fact.get("title"), fact.get("description"),
             fact.get("evidence_span"), fact.get("confidence"),
             json.dumps(fact.get("tags", [])), source_file))
        self.conn.execute(
            "UPDATE documents SET total_facts = total_facts + 1 WHERE id = ?",
            (document_id,))
        self.conn.commit()
        return cur.lastrowid

    def get_facts(self, document_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM facts WHERE document_id = ? ORDER BY id", (document_id,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["tags"] = json.loads(d.pop("tags_json") or "[]")
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
