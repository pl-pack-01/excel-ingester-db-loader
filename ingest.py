"""Read Excel files and write to SQLite."""

import re
from pathlib import Path

import pandas as pd


def read_excel(filepath: str | Path) -> pd.DataFrame:
    """Read an Excel file into a DataFrame."""
    return pd.read_excel(filepath, engine="openpyxl")


def table_name_from_filename(filename: str) -> str:
    """Derive a clean table name from a filename."""
    stem = Path(filename).stem.lower()
    sanitised = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")
    return sanitised or "data"


def normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise column names: strip, lowercase, spaces to underscores."""
    df = df.copy()
    df.columns = [
        col.strip().lower().replace(" ", "_") if isinstance(col, str) else str(col)
        for col in df.columns
    ]
    return df


def load_to_db(conn, df: pd.DataFrame, table_name: str, if_exists: str = "append") -> int:
    """Write a DataFrame to a SQLite table. Returns rows written.

    When appending, any columns present in the existing table but absent from
    the incoming DataFrame are added as NaN so the insert always succeeds.
    """
    if if_exists == "append":
        existing = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if existing:
            existing_cols = [
                row[1]
                for row in conn.execute(f"PRAGMA table_info([{table_name}])").fetchall()
            ]
            for col in existing_cols:
                if col not in df.columns:
                    df = df.copy()
                    df[col] = None
            # keep only columns the table knows about, in the right order
            df = df[[c for c in existing_cols if c in df.columns]]

    df.to_sql(table_name, conn, if_exists=if_exists, index=False)
    return len(df)
