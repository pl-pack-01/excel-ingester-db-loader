"""SQLite connection helper."""

import sqlite3
from pathlib import Path

DB_PATH = "db/data.sqlite"


def get_conn(db_path: str | None = None) -> sqlite3.Connection:
    """Return a SQLite connection, creating the db directory if needed."""
    path = db_path or DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def get_tables(conn: sqlite3.Connection) -> list[dict]:
    """Return all tables with their column names and row counts."""
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
