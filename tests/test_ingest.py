"""Tests for ingest module."""

import pandas as pd

from db import table_exists
from ingest import (
    file_hash,
    check_duplicate,
    read_excel,
    normalise_columns,
    write_to_db,
    log_ingest,
    ingest_file,
)


def test_file_hash_deterministic(sample_excel):
    h1 = file_hash(sample_excel)
    h2 = file_hash(sample_excel)
    assert h1 == h2
    assert len(h1) == 32  # MD5 hex digest


def test_check_duplicate_none(tmp_db):
    assert check_duplicate(tmp_db, "abc123") is None


def test_check_duplicate_found(tmp_db):
    log_ingest(tmp_db, "test.xlsx", "abc123", "sales", 10, "append")
    result = check_duplicate(tmp_db, "abc123")
    assert result is not None
    assert result["filename"] == "test.xlsx"


def test_read_excel(sample_excel):
    df = read_excel(sample_excel)
    assert len(df) == 3
    assert "Invoice Date" in df.columns


def test_normalise_columns():
    df = pd.DataFrame({"  First Name ": [1], "Amount $": [2], "Region": [3]})
    df = normalise_columns(df)
    assert list(df.columns) == ["first_name", "amount_$", "region"]


def test_write_to_db_append(tmp_db):
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    rows = write_to_db(tmp_db, df, "test_table", action="append")
    assert rows == 2
    assert table_exists(tmp_db, "test_table")

    # Append again
    rows2 = write_to_db(tmp_db, df, "test_table", action="append")
    assert rows2 == 2
    total = tmp_db.execute("SELECT count(*) FROM test_table").fetchone()[0]
    assert total == 4


def test_write_to_db_replace(tmp_db):
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    write_to_db(tmp_db, df, "test_table", action="append")
    write_to_db(tmp_db, df, "test_table", action="replace")
    total = tmp_db.execute("SELECT count(*) FROM test_table").fetchone()[0]
    assert total == 2


def test_ingest_file_full_pipeline(tmp_db, sample_excel):
    result = ingest_file(sample_excel, conn=tmp_db)
    assert result["filename"] == "sales_march_2026.xlsx"
    assert result["table"] == "sales"
    assert result["rows"] == 3
    assert result["action"] == "append"
    assert result["duplicate"] is False

    # Verify data in database
    assert table_exists(tmp_db, "sales")
    count = tmp_db.execute("SELECT count(*) FROM sales").fetchone()[0]
    assert count == 3

    # Verify ingest log
    log = tmp_db.execute("SELECT * FROM _ingest_log").fetchall()
    assert len(log) == 1


def test_ingest_file_duplicate_skip(tmp_db, sample_excel):
    # First ingest
    ingest_file(sample_excel, conn=tmp_db)
    # Second ingest with skip
    result = ingest_file(sample_excel, conn=tmp_db, action="skip")
    assert result["duplicate"] is True
    assert result["action"] == "skip"
    assert result["rows"] == 0


def test_ingest_file_duplicate_append(tmp_db, sample_excel):
    ingest_file(sample_excel, conn=tmp_db)
    result = ingest_file(sample_excel, conn=tmp_db, action="append")
    assert result["duplicate"] is True
    assert result["rows"] == 3
    count = tmp_db.execute("SELECT count(*) FROM sales").fetchone()[0]
    assert count == 6


def test_ingest_file_duplicate_replace(tmp_db, sample_excel):
    ingest_file(sample_excel, conn=tmp_db)
    result = ingest_file(sample_excel, conn=tmp_db, action="replace")
    assert result["duplicate"] is True
    assert result["rows"] == 3
    count = tmp_db.execute("SELECT count(*) FROM sales").fetchone()[0]
    assert count == 3


def test_ingest_inventory(tmp_db, sample_excel_inventory):
    result = ingest_file(sample_excel_inventory, conn=tmp_db)
    assert result["table"] == "inventory"
    assert result["rows"] == 3


def test_ingest_custom_table_name(tmp_db, sample_excel):
    result = ingest_file(sample_excel, conn=tmp_db, table_name="custom_table")
    assert result["table"] == "custom_table"
    assert table_exists(tmp_db, "custom_table")
