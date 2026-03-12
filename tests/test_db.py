"""Tests for db module."""

from db import get_conn, get_tables


def test_get_conn_creates_db(tmp_path):
    db_path = tmp_path / "sub" / "test.sqlite"
    conn = get_conn(str(db_path))
    assert db_path.exists()
    conn.close()


def test_get_tables_empty(tmp_db):
    assert get_tables(tmp_db) == []


def test_get_tables_after_insert(tmp_db):
    tmp_db.execute("CREATE TABLE demo (id INTEGER, name TEXT)")
    tmp_db.execute("INSERT INTO demo VALUES (1, 'a')")
    tmp_db.commit()
    tables = get_tables(tmp_db)
    assert len(tables) == 1
    assert tables[0]["name"] == "demo"
    assert tables[0]["row_count"] == 1
    assert "id" in tables[0]["columns"]
