"""Tests for db module."""

from db import get_conn, table_exists, get_tables, get_schema


def test_get_conn_creates_ingest_log(tmp_path):
    db_path = tmp_path / "test.sqlite"
    conn = get_conn(str(db_path))
    assert table_exists(conn, "_ingest_log")
    conn.close()


def test_table_exists_false(tmp_db):
    assert not table_exists(tmp_db, "nonexistent")


def test_get_tables_empty(tmp_db):
    tables = get_tables(tmp_db)
    # Only _ingest_log should exist
    assert len(tables) == 1
    assert tables[0]["name"] == "_ingest_log"


def test_get_schema(tmp_db):
    schema = get_schema(tmp_db)
    assert "_ingest_log" in schema
    assert "filename" in schema["_ingest_log"]
