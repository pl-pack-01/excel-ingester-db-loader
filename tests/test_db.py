"""Tests for db module."""

from db import get_conn, get_tables, ensure_ingest_log, is_already_ingested, record_ingest


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


# ── Ingest log tests ──────────────────────────────────────────────────────────

def test_ensure_ingest_log_creates_table(tmp_db):
    ensure_ingest_log(tmp_db)
    tables = [r["name"] for r in tmp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "_ingest_log" in tables


def test_ensure_ingest_log_idempotent(tmp_db):
    ensure_ingest_log(tmp_db)
    ensure_ingest_log(tmp_db)  # second call must not raise


def test_not_already_ingested(tmp_db):
    ensure_ingest_log(tmp_db)
    assert not is_already_ingested(tmp_db, "report.xlsx", "2026-03-01 09:00:00")


def test_record_and_detect_duplicate(tmp_db):
    ensure_ingest_log(tmp_db)
    record_ingest(tmp_db, "report.xlsx", "2026-03-01 09:00:00", "report", 50)
    assert is_already_ingested(tmp_db, "report.xlsx", "2026-03-01 09:00:00")


def test_different_received_at_is_not_duplicate(tmp_db):
    ensure_ingest_log(tmp_db)
    record_ingest(tmp_db, "report.xlsx", "2026-03-01 09:00:00", "report", 50)
    assert not is_already_ingested(tmp_db, "report.xlsx", "2026-03-02 09:00:00")


def test_record_ingest_ignore_on_duplicate(tmp_db):
    """Inserting the same (filename, received_at) twice must not raise."""
    ensure_ingest_log(tmp_db)
    record_ingest(tmp_db, "report.xlsx", "2026-03-01 09:00:00", "report", 50)
    record_ingest(tmp_db, "report.xlsx", "2026-03-01 09:00:00", "report", 50)
    count = tmp_db.execute("SELECT count(*) FROM _ingest_log").fetchone()[0]
    assert count == 1
