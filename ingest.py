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


def load_to_db(conn, df: pd.DataFrame, table_name: str, if_exists: str = "replace") -> int:
    """Write a DataFrame to a SQLite table. Returns rows written."""
    df.to_sql(table_name, conn, if_exists=if_exists, index=False)
    return len(df)
