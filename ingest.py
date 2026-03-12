"""Core ingest logic — read Excel files, check duplicates, write to SQLite."""

import hashlib
import sqlite3
from pathlib import Path

import pandas as pd

from db import get_conn
from router import route_filename


def file_hash(filepath: str | Path) -> str:
    """Return the MD5 hex digest of a file."""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def check_duplicate(conn: sqlite3.Connection, fhash: str) -> dict | None:
    """Check if a file with this hash has been ingested before.

    Returns the log row as a dict if found, None otherwise.
    """
    row = conn.execute(
        "SELECT * FROM _ingest_log WHERE file_hash = ? ORDER BY ingested_at DESC LIMIT 1",
        (fhash,),
    ).fetchone()
    if row:
        return dict(row)
    return None


def read_excel(filepath: str | Path) -> pd.DataFrame:
    """Read an Excel file into a DataFrame."""
    return pd.read_excel(filepath, engine="openpyxl")


def normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise column names: strip, lowercase, spaces to underscores."""
    df.columns = [
        col.strip().lower().replace(" ", "_") if isinstance(col, str) else str(col)
        for col in df.columns
    ]
    return df


def write_to_db(
    conn: sqlite3.Connection,
    df: pd.DataFrame,
    table_name: str,
    action: str = "append",
) -> int:
    """Write a DataFrame to a SQLite table.

    action: 'append' adds rows, 'replace' drops existing data first.
    Returns the number of rows written.
    """
    if action == "replace":
        conn.execute(f"DROP TABLE IF EXISTS [{table_name}]")
        conn.commit()

    if_exists = "append" if action == "append" else "replace"
    df.to_sql(table_name, conn, if_exists=if_exists, index=False)
    return len(df)


def log_ingest(
    conn: sqlite3.Connection,
    filename: str,
    fhash: str,
    table_name: str,
    rows_written: int,
    action: str,
) -> None:
    """Record an ingest event in _ingest_log."""
    conn.execute(
        "INSERT INTO _ingest_log (filename, file_hash, target_table, rows_written, action) VALUES (?, ?, ?, ?, ?)",
        (filename, fhash, table_name, rows_written, action),
    )
    conn.commit()


def ingest_file(
    filepath: str | Path,
    conn: sqlite3.Connection | None = None,
    action: str = "append",
    table_name: str | None = None,
) -> dict:
    """Full ingest pipeline for a single Excel file.

    Returns a summary dict with keys: filename, table, rows, hash, action, duplicate.
    """
    filepath = Path(filepath)
    own_conn = conn is None
    if own_conn:
        conn = get_conn()

    try:
        fhash = file_hash(filepath)
        duplicate = check_duplicate(conn, fhash)

        if duplicate and action == "skip":
            return {
                "filename": filepath.name,
                "table": duplicate["target_table"],
                "rows": 0,
                "hash": fhash,
                "action": "skip",
                "duplicate": True,
            }

        target = table_name or route_filename(filepath.name)
        df = read_excel(filepath)
        df = normalise_columns(df)
        rows = write_to_db(conn, df, target, action=action)
        log_ingest(conn, filepath.name, fhash, target, rows, action)

        return {
            "filename": filepath.name,
            "table": target,
            "rows": rows,
            "hash": fhash,
            "action": action,
            "duplicate": duplicate is not None,
        }
    finally:
        if own_conn:
            conn.close()
