"""Tests for ingest module."""

import pandas as pd

from ingest import read_excel, table_name_from_filename, normalise_columns, load_to_db


def test_read_excel(sample_excel):
    df = read_excel(sample_excel)
    assert len(df) == 3
    assert "Invoice Date" in df.columns


def test_table_name_from_filename():
    assert table_name_from_filename("sales_q1_2026.xlsx") == "sales_q1_2026"
    assert table_name_from_filename("My Report (final).xlsx") == "my_report_final"
    assert table_name_from_filename("INVENTORY.xlsx") == "inventory"


def test_normalise_columns():
    df = pd.DataFrame({"  First Name ": [1], "Amount $": [2], "Region": [3]})
    df = normalise_columns(df)
    assert list(df.columns) == ["first_name", "amount_$", "region"]


def test_load_to_db_replace(tmp_db):
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    rows = load_to_db(tmp_db, df, "test_table")
    assert rows == 2

    # Replace overwrites
    load_to_db(tmp_db, df, "test_table", if_exists="replace")
    total = tmp_db.execute("SELECT count(*) FROM test_table").fetchone()[0]
    assert total == 2


def test_load_to_db_append(tmp_db):
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    load_to_db(tmp_db, df, "test_table")
    load_to_db(tmp_db, df, "test_table", if_exists="append")
    total = tmp_db.execute("SELECT count(*) FROM test_table").fetchone()[0]
    assert total == 4


def test_full_pipeline(tmp_db, sample_excel):
    df = read_excel(sample_excel)
    df = normalise_columns(df)
    table = table_name_from_filename(sample_excel.name)
    rows = load_to_db(tmp_db, df, table)
    assert rows == 3
    count = tmp_db.execute(f"SELECT count(*) FROM [{table}]").fetchone()[0]
    assert count == 3
