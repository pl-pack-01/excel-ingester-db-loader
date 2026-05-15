"""SQLite helpers for ServiceNow snapshot ingestion and trend reporting."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

DB_PATH = "db/data.sqlite"


def get_conn(db_path: str | None = None) -> sqlite3.Connection:
    """Return a SQLite connection, creating parent directories as needed."""
    path = db_path or DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def get_tables(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all user tables/views with columns and row counts where possible."""
    rows = conn.execute(
        """
        SELECT name, type
        FROM sqlite_master
        WHERE type IN ('table', 'view')
          AND name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()

    tables: list[dict[str, Any]] = []
    for row in rows:
        name = row["name"]
        obj_type = row["type"]
        cols = [c["name"] for c in conn.execute(f"PRAGMA table_info([{name}])").fetchall()]
        try:
            count = conn.execute(f"SELECT count(*) FROM [{name}]").fetchone()[0]
        except sqlite3.OperationalError:
            count = None
        tables.append({"name": name, "type": obj_type, "columns": cols, "row_count": count})
    return tables


def drop_table(conn: sqlite3.Connection, table_name: str) -> None:
    """Drop a table by name."""
    conn.execute(f"DROP TABLE IF EXISTS [{table_name}]")
    conn.commit()


# --- Legacy ingest log (kept for compatibility) -----------------------------

def ensure_ingest_log(conn: sqlite3.Connection) -> None:
    """Create the legacy _ingest_log table if absent."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _ingest_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            filename     TEXT NOT NULL,
            received_at  TEXT NOT NULL,
            target_table TEXT NOT NULL,
            rows_loaded  INTEGER NOT NULL,
            logged_at    TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS _ingest_log_uq "
        "ON _ingest_log (filename, received_at)"
    )
    conn.commit()


def is_already_ingested(conn: sqlite3.Connection, filename: str, received_at: str) -> bool:
    """Return True if this (filename, received_at) pair was imported already."""
    row = conn.execute(
        "SELECT 1 FROM _ingest_log WHERE filename = ? AND received_at = ?",
        (filename, received_at),
    ).fetchone()
    return row is not None


def record_ingest(
    conn: sqlite3.Connection,
    filename: str,
    received_at: str,
    target_table: str,
    rows_loaded: int,
) -> None:
    """Write a successful import to the legacy ingest log."""
    conn.execute(
        "INSERT OR IGNORE INTO _ingest_log "
        "(filename, received_at, target_table, rows_loaded, logged_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            filename,
            received_at,
            target_table,
            rows_loaded,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()


# --- ServiceNow schema and reporting views ----------------------------------

def _ensure_column(conn: sqlite3.Connection, table: str, column: str, col_type: str) -> None:
    existing = {
        row["name"] for row in conn.execute(f"PRAGMA table_info([{table}])").fetchall()
    }
    if column not in existing:
        conn.execute(f"ALTER TABLE [{table}] ADD COLUMN [{column}] {col_type}")


def ensure_servicenow_schema(conn: sqlite3.Connection) -> None:
    """Create base ServiceNow snapshot tables and trend views."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sn_sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            pulled_at TEXT NOT NULL,
            since_days INTEGER NOT NULL,
            incident_count INTEGER NOT NULL,
            request_item_count INTEGER NOT NULL,
            change_request_count INTEGER NOT NULL DEFAULT 0,
            problem_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            notes TEXT
        )
        """
    )

    _ensure_column(conn, "sn_sync_runs", "change_request_count", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "sn_sync_runs", "problem_count", "INTEGER NOT NULL DEFAULT 0")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sn_incident_snapshot (
            run_id INTEGER NOT NULL,
            snapshot_date TEXT NOT NULL,
            pulled_at TEXT NOT NULL,
            sys_id TEXT NOT NULL,
            number TEXT,
            opened_at TEXT,
            sys_created_on TEXT,
            sys_updated_on TEXT,
            resolved_at TEXT,
            closed_at TEXT,
            state TEXT,
            priority TEXT,
            severity TEXT,
            impact TEXT,
            urgency TEXT,
            category TEXT,
            subcategory TEXT,
            assignment_group TEXT,
            assigned_to TEXT,
            caller_id TEXT,
            short_description TEXT,
            raw_payload TEXT,
            UNIQUE(snapshot_date, sys_id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sn_request_item_snapshot (
            run_id INTEGER NOT NULL,
            snapshot_date TEXT NOT NULL,
            pulled_at TEXT NOT NULL,
            sys_id TEXT NOT NULL,
            number TEXT,
            request TEXT,
            opened_at TEXT,
            sys_created_on TEXT,
            sys_updated_on TEXT,
            closed_at TEXT,
            state TEXT,
            priority TEXT,
            cat_item TEXT,
            short_description TEXT,
            assignment_group TEXT,
            assigned_to TEXT,
            requested_for TEXT,
            raw_payload TEXT,
            UNIQUE(snapshot_date, sys_id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sn_change_request_snapshot (
            run_id INTEGER NOT NULL,
            snapshot_date TEXT NOT NULL,
            pulled_at TEXT NOT NULL,
            sys_id TEXT NOT NULL,
            number TEXT,
            opened_at TEXT,
            sys_created_on TEXT,
            sys_updated_on TEXT,
            start_date TEXT,
            end_date TEXT,
            state TEXT,
            type TEXT,
            risk TEXT,
            priority TEXT,
            category TEXT,
            assignment_group TEXT,
            assigned_to TEXT,
            short_description TEXT,
            raw_payload TEXT,
            UNIQUE(snapshot_date, sys_id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sn_problem_snapshot (
            run_id INTEGER NOT NULL,
            snapshot_date TEXT NOT NULL,
            pulled_at TEXT NOT NULL,
            sys_id TEXT NOT NULL,
            number TEXT,
            opened_at TEXT,
            sys_created_on TEXT,
            sys_updated_on TEXT,
            closed_at TEXT,
            state TEXT,
            priority TEXT,
            known_error TEXT,
            category TEXT,
            assignment_group TEXT,
            assigned_to TEXT,
            short_description TEXT,
            raw_payload TEXT,
            UNIQUE(snapshot_date, sys_id)
        )
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sn_incident_snapshot_date
        ON sn_incident_snapshot (snapshot_date)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sn_request_snapshot_date
        ON sn_request_item_snapshot (snapshot_date)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sn_change_snapshot_date
        ON sn_change_request_snapshot (snapshot_date)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sn_problem_snapshot_date
        ON sn_problem_snapshot (snapshot_date)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sn_incident_number
        ON sn_incident_snapshot (number)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sn_request_number
        ON sn_request_item_snapshot (number)
        """
    )

    ensure_servicenow_views(conn)
    conn.commit()


def ensure_servicenow_views(conn: sqlite3.Connection) -> None:
    """Create trend and latest-state views used by the app and BI tools."""
    conn.execute("DROP VIEW IF EXISTS v_incident_latest")
    conn.execute(
        """
        CREATE VIEW v_incident_latest AS
        WITH ranked AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY sys_id
                    ORDER BY snapshot_date DESC, pulled_at DESC
                ) AS rn
            FROM sn_incident_snapshot
        )
        SELECT
            sys_id,
            number,
            state,
            priority,
            category,
            subcategory,
            assignment_group,
            assigned_to,
            opened_at,
            sys_updated_on,
            closed_at,
            snapshot_date AS last_snapshot_date,
            pulled_at AS last_pulled_at,
            ROUND(JULIANDAY('now') - JULIANDAY(COALESCE(opened_at, sys_created_on)), 1) AS age_days,
            CASE
                WHEN closed_at IS NULL OR closed_at = '' THEN 1
                ELSE 0
            END AS is_open
        FROM ranked
        WHERE rn = 1
        """
    )

    conn.execute("DROP VIEW IF EXISTS v_request_item_latest")
    conn.execute(
        """
        CREATE VIEW v_request_item_latest AS
        WITH ranked AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY sys_id
                    ORDER BY snapshot_date DESC, pulled_at DESC
                ) AS rn
            FROM sn_request_item_snapshot
        )
        SELECT
            sys_id,
            number,
            request,
            state,
            priority,
            cat_item,
            assignment_group,
            assigned_to,
            opened_at,
            sys_updated_on,
            closed_at,
            snapshot_date AS last_snapshot_date,
            pulled_at AS last_pulled_at,
            ROUND(JULIANDAY('now') - JULIANDAY(COALESCE(opened_at, sys_created_on)), 1) AS age_days,
            CASE
                WHEN closed_at IS NULL OR closed_at = '' THEN 1
                ELSE 0
            END AS is_open
        FROM ranked
        WHERE rn = 1
        """
    )

    conn.execute("DROP VIEW IF EXISTS v_incident_trends_daily")
    conn.execute(
        """
        CREATE VIEW v_incident_trends_daily AS
        SELECT
            snapshot_date,
            COALESCE(NULLIF(category, ''), 'Uncategorised') AS category,
            COALESCE(NULLIF(state, ''), 'Unknown') AS state,
            COUNT(*) AS ticket_count,
            SUM(CASE WHEN closed_at IS NULL OR closed_at = '' THEN 1 ELSE 0 END) AS open_count,
            SUM(CASE WHEN closed_at IS NOT NULL AND closed_at <> '' THEN 1 ELSE 0 END) AS closed_count
        FROM sn_incident_snapshot
        GROUP BY snapshot_date, category, state
        """
    )

    conn.execute("DROP VIEW IF EXISTS v_request_type_trends_daily")
    conn.execute(
        """
        CREATE VIEW v_request_type_trends_daily AS
        SELECT
            snapshot_date,
            COALESCE(NULLIF(cat_item, ''), 'Unspecified Item') AS request_type,
            COALESCE(NULLIF(state, ''), 'Unknown') AS state,
            COUNT(*) AS request_count,
            SUM(CASE WHEN closed_at IS NULL OR closed_at = '' THEN 1 ELSE 0 END) AS open_count,
            SUM(CASE WHEN closed_at IS NOT NULL AND closed_at <> '' THEN 1 ELSE 0 END) AS closed_count
        FROM sn_request_item_snapshot
        GROUP BY snapshot_date, request_type, state
        """
    )

    conn.execute("DROP VIEW IF EXISTS v_incident_sla_daily")
    conn.execute(
        """
        CREATE VIEW v_incident_sla_daily AS
        WITH durations AS (
            SELECT
                snapshot_date,
                COALESCE(NULLIF(priority, ''), 'Unknown') AS priority,
                (
                    JULIANDAY(COALESCE(resolved_at, closed_at, sys_updated_on))
                    - JULIANDAY(COALESCE(opened_at, sys_created_on))
                ) * 24.0 AS resolution_hours
            FROM sn_incident_snapshot
            WHERE COALESCE(opened_at, sys_created_on) IS NOT NULL
              AND COALESCE(resolved_at, closed_at, sys_updated_on) IS NOT NULL
        )
        SELECT
            snapshot_date,
            priority,
            COUNT(*) AS resolved_count,
            ROUND(AVG(resolution_hours), 2) AS avg_resolution_hours,
            SUM(
                CASE
                    WHEN resolution_hours > CASE priority
                        WHEN '1' THEN 4
                        WHEN '2' THEN 8
                        WHEN '3' THEN 24
                        WHEN '4' THEN 72
                        ELSE 120
                    END THEN 1
                    ELSE 0
                END
            ) AS breached_count
        FROM durations
        GROUP BY snapshot_date, priority
        """
    )

    conn.execute("DROP VIEW IF EXISTS v_change_request_trends_daily")
    conn.execute(
        """
        CREATE VIEW v_change_request_trends_daily AS
        SELECT
            snapshot_date,
            COALESCE(NULLIF(type, ''), 'Unspecified Type') AS change_type,
            COALESCE(NULLIF(state, ''), 'Unknown') AS state,
            COUNT(*) AS change_count,
            SUM(
                CASE
                    WHEN LOWER(COALESCE(state, '')) IN ('closed', 'complete', 'completed', 'cancelled', 'canceled')
                        THEN 0
                    ELSE 1
                END
            ) AS open_count
        FROM sn_change_request_snapshot
        GROUP BY snapshot_date, change_type, state
        """
    )

    conn.execute("DROP VIEW IF EXISTS v_problem_trends_daily")
    conn.execute(
        """
        CREATE VIEW v_problem_trends_daily AS
        SELECT
            snapshot_date,
            COALESCE(NULLIF(category, ''), 'Uncategorised') AS category,
            COALESCE(NULLIF(state, ''), 'Unknown') AS state,
            COUNT(*) AS problem_count,
            SUM(CASE WHEN closed_at IS NULL OR closed_at = '' THEN 1 ELSE 0 END) AS open_count
        FROM sn_problem_snapshot
        GROUP BY snapshot_date, category, state
        """
    )

    conn.execute("DROP VIEW IF EXISTS v_snapshot_run_summary")
    conn.execute(
        """
        CREATE VIEW v_snapshot_run_summary AS
        SELECT
            id,
            snapshot_date,
            pulled_at,
            since_days,
            incident_count,
            request_item_count,
            change_request_count,
            problem_count,
            status,
            notes
        FROM sn_sync_runs
        ORDER BY id DESC
        """
    )


def _insert_sync_run(
    conn: sqlite3.Connection,
    *,
    snapshot_date: str,
    pulled_at: str,
    since_days: int,
    incident_count: int,
    request_item_count: int,
    change_request_count: int,
    problem_count: int,
    status: str,
    notes: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO sn_sync_runs (
            snapshot_date,
            pulled_at,
            since_days,
            incident_count,
            request_item_count,
            change_request_count,
            problem_count,
            status,
            notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_date,
            pulled_at,
            since_days,
            incident_count,
            request_item_count,
            change_request_count,
            problem_count,
            status,
            notes,
        ),
    )
    return int(cur.lastrowid)


def store_servicenow_snapshot(conn: sqlite3.Connection, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Persist one ServiceNow snapshot pull into normalized snapshot tables."""
    ensure_servicenow_schema(conn)

    snapshot_date = snapshot.get("snapshot_date") or datetime.utcnow().date().isoformat()
    pulled_at = snapshot.get("pulled_at") or datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    since_days = int(snapshot.get("since_days", 30))

    incidents = snapshot.get("incidents", []) or []
    request_items = snapshot.get("request_items", []) or []
    change_requests = snapshot.get("change_requests", []) or []
    problems = snapshot.get("problems", []) or []

    run_id = _insert_sync_run(
        conn,
        snapshot_date=snapshot_date,
        pulled_at=pulled_at,
        since_days=since_days,
        incident_count=len(incidents),
        request_item_count=len(request_items),
        change_request_count=len(change_requests),
        problem_count=len(problems),
        status="success",
        notes=snapshot.get("message"),
    )

    for row in incidents:
        conn.execute(
            """
            INSERT OR REPLACE INTO sn_incident_snapshot (
                run_id, snapshot_date, pulled_at, sys_id, number,
                opened_at, sys_created_on, sys_updated_on, resolved_at, closed_at,
                state, priority, severity, impact, urgency,
                category, subcategory, assignment_group, assigned_to, caller_id,
                short_description, raw_payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                snapshot_date,
                pulled_at,
                str(row.get("sys_id", "")),
                row.get("number"),
                row.get("opened_at"),
                row.get("sys_created_on"),
                row.get("sys_updated_on"),
                row.get("resolved_at"),
                row.get("closed_at"),
                row.get("state"),
                row.get("priority"),
                row.get("severity"),
                row.get("impact"),
                row.get("urgency"),
                row.get("category"),
                row.get("subcategory"),
                row.get("assignment_group"),
                row.get("assigned_to"),
                row.get("caller_id"),
                row.get("short_description"),
                json.dumps(row, default=str),
            ),
        )

    for row in request_items:
        conn.execute(
            """
            INSERT OR REPLACE INTO sn_request_item_snapshot (
                run_id, snapshot_date, pulled_at, sys_id, number,
                request, opened_at, sys_created_on, sys_updated_on, closed_at,
                state, priority, cat_item, short_description, assignment_group,
                assigned_to, requested_for, raw_payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                snapshot_date,
                pulled_at,
                str(row.get("sys_id", "")),
                row.get("number"),
                row.get("request"),
                row.get("opened_at"),
                row.get("sys_created_on"),
                row.get("sys_updated_on"),
                row.get("closed_at"),
                row.get("state"),
                row.get("priority"),
                row.get("cat_item"),
                row.get("short_description"),
                row.get("assignment_group"),
                row.get("assigned_to"),
                row.get("requested_for"),
                json.dumps(row, default=str),
            ),
        )

    for row in change_requests:
        conn.execute(
            """
            INSERT OR REPLACE INTO sn_change_request_snapshot (
                run_id, snapshot_date, pulled_at, sys_id, number,
                opened_at, sys_created_on, sys_updated_on, start_date, end_date,
                state, type, risk, priority, category,
                assignment_group, assigned_to, short_description, raw_payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                snapshot_date,
                pulled_at,
                str(row.get("sys_id", "")),
                row.get("number"),
                row.get("opened_at"),
                row.get("sys_created_on"),
                row.get("sys_updated_on"),
                row.get("start_date"),
                row.get("end_date"),
                row.get("state"),
                row.get("type"),
                row.get("risk"),
                row.get("priority"),
                row.get("category"),
                row.get("assignment_group"),
                row.get("assigned_to"),
                row.get("short_description"),
                json.dumps(row, default=str),
            ),
        )

    for row in problems:
        conn.execute(
            """
            INSERT OR REPLACE INTO sn_problem_snapshot (
                run_id, snapshot_date, pulled_at, sys_id, number,
                opened_at, sys_created_on, sys_updated_on, closed_at, state,
                priority, known_error, category, assignment_group,
                assigned_to, short_description, raw_payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                snapshot_date,
                pulled_at,
                str(row.get("sys_id", "")),
                row.get("number"),
                row.get("opened_at"),
                row.get("sys_created_on"),
                row.get("sys_updated_on"),
                row.get("closed_at"),
                row.get("state"),
                row.get("priority"),
                row.get("known_error"),
                row.get("category"),
                row.get("assignment_group"),
                row.get("assigned_to"),
                row.get("short_description"),
                json.dumps(row, default=str),
            ),
        )

    conn.commit()
    return {
        "run_id": run_id,
        "snapshot_date": snapshot_date,
        "incident_rows": len(incidents),
        "request_item_rows": len(request_items),
        "change_request_rows": len(change_requests),
        "problem_rows": len(problems),
    }
