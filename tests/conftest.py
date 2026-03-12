"""Shared test fixtures."""

import pandas as pd
import pytest

from db import get_conn


@pytest.fixture
def tmp_db(tmp_path):
    """Yield a SQLite connection to a temporary database."""
    conn = get_conn(str(tmp_path / "test.sqlite"))
    yield conn
    conn.close()


@pytest.fixture
def sample_excel(tmp_path):
    """Create a sample .xlsx file and return its path."""
    df = pd.DataFrame({
        "Invoice Date": ["2026-01-01", "2026-01-02", "2026-01-03"],
        "Customer": ["Acme Corp", "Globex", "Initech"],
        "Amount": [1200, 3400, 5600],
    })
    path = tmp_path / "sales_q1_2026.xlsx"
    df.to_excel(path, index=False, engine="openpyxl")
    return path
