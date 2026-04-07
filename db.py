"""SQLite connection helper."""

import sqlite3
from datetime import datetime
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


# ── Ingest log ───────────────────────────────────────────────────────────────

def ensure_ingest_log(conn: sqlite3.Connection) -> None:
    """Create the _ingest_log table if it doesn't already exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _ingest_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            filename    TEXT    NOT NULL,
            received_at TEXT    NOT NULL,
            target_table TEXT   NOT NULL,
            rows_loaded INTEGER NOT NULL,
            logged_at   TEXT    NOT NULL
        )
    """)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS _ingest_log_uq "
        "ON _ingest_log (filename, received_at)"
    )
    conn.commit()


def is_already_ingested(conn: sqlite3.Connection, filename: str, received_at: str) -> bool:
    """Return True if this (filename, received_at) pair has been imported before."""
    row = conn.execute(
        "SELECT 1 FROM _ingest_log WHERE filename = ? AND received_at = ?",
        (filename, received_at),
    ).fetchone()
    return row is not None


def drop_table(conn: sqlite3.Connection, table_name: str) -> None:
    """Drop a table by name."""
    conn.execute(f"DROP TABLE IF EXISTS [{table_name}]")
    conn.commit()


def record_ingest(
    conn: sqlite3.Connection,
    filename: str,
    received_at: str,
    target_table: str,
    rows_loaded: int,
) -> None:
    """Write a successful import to the ingest log."""
    conn.execute(
        "INSERT OR IGNORE INTO _ingest_log "
        "(filename, received_at, target_table, rows_loaded, logged_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (filename, received_at, target_table, rows_loaded,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()


# ── Reporting views ──────────────────────────────────────────────────────────

def ensure_incidents_view(conn: sqlite3.Connection) -> None:
    """Create (or replace) the v_incidents_latest view for Power BI reporting.

    The raw incidents table accumulates one row per daily email snapshot, so
    the same incident number appears ~10 times on average.  This view reduces
    that to a single row per incident number — the most-recently-ingested
    snapshot — and adds three calculated fields:

      duration_days        — days from ticket created → latest updated
      age_days             — days from ticket created → today
      days_since_last_update — days from latest updated → today
    """
    conn.execute("DROP VIEW IF EXISTS v_incidents_latest")
    conn.execute("""
        CREATE VIEW v_incidents_latest AS
        WITH ranked AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY number
                    ORDER BY _ingested_at DESC, updated DESC
                ) AS rn
            FROM automated_sas_cloud_all_incidents
        )
        SELECT
            number,
            reporter_lob,
            territory,
            region,
            configuration_item,
            short_summary,
            state,
            assignment_group,
            assigned_to,
            created,
            updated,
            ROUND(JULIANDAY(updated) - JULIANDAY(created), 1)  AS duration_days,
            ROUND(JULIANDAY('now')   - JULIANDAY(created), 0)  AS age_days,
            ROUND(JULIANDAY('now')   - JULIANDAY(updated), 0)  AS days_since_last_update,
            _ingested_at                                        AS last_seen_at
        FROM ranked
        WHERE rn = 1
    """)
    conn.commit()
