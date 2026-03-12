"""Shared test fixtures."""

import tempfile
from pathlib import Path

import pandas as pd
import pytest

from db import get_conn


@pytest.fixture
def tmp_db(tmp_path):
    """Yield a SQLite connection to a temporary database."""
    db_path = tmp_path / "test.sqlite"
    conn = get_conn(str(db_path))
    yield conn
    conn.close()


@pytest.fixture
def sample_excel(tmp_path):
    """Create a sample .xlsx file and return its path."""
    df = pd.DataFrame({
        "Invoice Date": ["2026-01-01", "2026-01-02", "2026-01-03"],
        "Customer": ["Acme Corp", "Globex", "Initech"],
        "Amount": [1200, 3400, 5600],
        "Region": ["NE", "SW", "MW"],
    })
    path = tmp_path / "sales_march_2026.xlsx"
    df.to_excel(path, index=False, engine="openpyxl")
    return path


@pytest.fixture
def sample_excel_inventory(tmp_path):
    """Create an inventory .xlsx file and return its path."""
    df = pd.DataFrame({
        "SKU": ["A001", "A002", "B001"],
        "Product Name": ["Widget", "Gadget", "Sprocket"],
        "Quantity": [100, 250, 75],
    })
    path = tmp_path / "inventory_q1.xlsx"
    df.to_excel(path, index=False, engine="openpyxl")
    return path
