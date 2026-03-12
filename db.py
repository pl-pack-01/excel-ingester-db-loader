"""Database module — isolates all connection logic to enable SQLite → PostgreSQL swap."""

import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "db/data.sqlite")

INGEST_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS _ingest_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    target_table TEXT NOT NULL,
    rows_written INTEGER NOT NULL,
    action TEXT NOT NULL,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def get_conn(db_path: str | None = None) -> sqlite3.Connection:
    """Return a SQLite connection, creating the db directory and ingest log table if needed."""
    path = db_path or DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(INGEST_LOG_SCHEMA)
    conn.commit()
    return conn


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Check whether a table exists in the database."""
    row = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row[0] > 0


def get_tables(conn: sqlite3.Connection) -> list[dict]:
    """Return all user tables with column names and row counts."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    tables = []
    for row in rows:
        name = row["name"]
        cols = [c["name"] for c in conn.execute(f"PRAGMA table_info([{name}])").fetchall()]
        count = conn.execute(f"SELECT count(*) FROM [{name}]").fetchone()[0]
        tables.append({"name": name, "columns": cols, "row_count": count})
    return tables


def get_schema(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Return {table_name: [column_names]} for all user tables (used by AI prompt builder)."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    schema = {}
    for row in rows:
        name = row["name"]
        cols = [c["name"] for c in conn.execute(f"PRAGMA table_info([{name}])").fetchall()]
        schema[name] = cols
    return schema
