"""SQLite helpers for ServiceNow snapshot ingestion and trend reporting."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

DB_PATH = "db/data.sqlite"


def _to_db_value(value: Any) -> Any:
    """Convert nested API values into SQLite-safe scalar text where needed."""
    if isinstance(value, dict):
        if value.get("display_value") not in (None, ""):
            return str(value.get("display_value"))
        if value.get("value") not in (None, ""):
            return str(value.get("value"))
        if "display_value" in value or "value" in value:
            return None
        return json.dumps(value, default=str)
    if isinstance(value, list):
        return json.dumps(value, default=str)
    return value


def get_conn(db_path: str | None = None) -> sqlite3.Connection:
    """Return a SQLite connection, creating parent directories as needed."""
    path = db_path or DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    # WAL + busy timeout improves concurrent reads while sync writes are active.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
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
            active TEXT,
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
            active TEXT,
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
            cmdb_ci_sys_id TEXT,
            cmdb_ci_name TEXT,
            cmdb_ci_lob_sys_id TEXT,
            cmdb_ci_lob TEXT,
            cmdb_ci_lob_details_sys_id TEXT,
            cmdb_ci_lob_details TEXT,
            cmdb_ci_customer_relationship_sys_id TEXT,
            cmdb_ci_customer_relationship TEXT,
            cmdb_ci_customer_relationship_region TEXT,
            cmdb_ci_customer_relationship_territory TEXT,
            cmdb_ci_region TEXT,
            cmdb_ci_territory TEXT,
            cmdb_ci_region_source TEXT,
            cmdb_ci_territory_source TEXT,
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
    _ensure_column(conn, "sn_problem_snapshot", "cmdb_ci_sys_id", "TEXT")
    _ensure_column(conn, "sn_problem_snapshot", "cmdb_ci_name", "TEXT")
    _ensure_column(conn, "sn_problem_snapshot", "cmdb_ci_lob_sys_id", "TEXT")
    _ensure_column(conn, "sn_problem_snapshot", "cmdb_ci_lob", "TEXT")
    _ensure_column(conn, "sn_problem_snapshot", "cmdb_ci_lob_details_sys_id", "TEXT")
    _ensure_column(conn, "sn_problem_snapshot", "cmdb_ci_lob_details", "TEXT")
    _ensure_column(conn, "sn_problem_snapshot", "cmdb_ci_customer_relationship_sys_id", "TEXT")
    _ensure_column(conn, "sn_problem_snapshot", "cmdb_ci_customer_relationship", "TEXT")
    _ensure_column(conn, "sn_problem_snapshot", "cmdb_ci_region", "TEXT")
    _ensure_column(conn, "sn_problem_snapshot", "cmdb_ci_territory", "TEXT")
    _ensure_column(conn, "sn_problem_snapshot", "cmdb_ci_customer_relationship_region", "TEXT")
    _ensure_column(conn, "sn_problem_snapshot", "cmdb_ci_customer_relationship_territory", "TEXT")
    _ensure_column(conn, "sn_problem_snapshot", "cmdb_ci_region_source", "TEXT")
    _ensure_column(conn, "sn_problem_snapshot", "cmdb_ci_territory_source", "TEXT")
    _ensure_column(conn, "sn_incident_snapshot", "active", "TEXT")
    _ensure_column(conn, "sn_request_item_snapshot", "active", "TEXT")
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
            active,
            snapshot_date AS last_snapshot_date,
            pulled_at AS last_pulled_at,
            ROUND(JULIANDAY('now') - JULIANDAY(COALESCE(opened_at, sys_created_on)), 1) AS age_days,
            CASE
                WHEN LOWER(COALESCE(active, '')) IN ('true', '1', 'yes') THEN 1
                WHEN active IS NULL OR active = '' THEN CASE WHEN closed_at IS NULL OR closed_at = '' THEN 1 ELSE 0 END
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
            active,
            snapshot_date AS last_snapshot_date,
            pulled_at AS last_pulled_at,
            ROUND(JULIANDAY('now') - JULIANDAY(COALESCE(opened_at, sys_created_on)), 1) AS age_days,
            CASE
                WHEN LOWER(COALESCE(active, '')) IN ('true', '1', 'yes') THEN 1
                WHEN active IS NULL OR active = '' THEN CASE WHEN closed_at IS NULL OR closed_at = '' THEN 1 ELSE 0 END
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

    conn.execute("DROP VIEW IF EXISTS v_incident_reduction_monthly")
    conn.execute(
        """
        CREATE VIEW v_incident_reduction_monthly AS
        WITH latest_snapshot AS (
            SELECT MAX(snapshot_date) AS snapshot_date
            FROM sn_incident_snapshot
        ),
        ranked AS (
            SELECT
                i.*,
                ROW_NUMBER() OVER (
                    PARTITION BY i.sys_id
                    ORDER BY i.snapshot_date DESC, i.pulled_at DESC
                ) AS rn
            FROM sn_incident_snapshot i
            JOIN latest_snapshot l ON i.snapshot_date = l.snapshot_date
        )
        SELECT
            substr(COALESCE(opened_at, sys_created_on), 1, 7) AS period_month,
            COALESCE(NULLIF(category, ''), 'Uncategorised') AS category,
            COUNT(*) AS incident_count,
            SUM(
                CASE
                    WHEN closed_at IS NULL OR closed_at = '' THEN 1
                    ELSE 0
                END
            ) AS open_count,
            SUM(
                CASE
                    WHEN closed_at IS NOT NULL AND closed_at <> '' THEN 1
                    ELSE 0
                END
            ) AS closed_count
        FROM ranked
        WHERE rn = 1
          AND COALESCE(opened_at, sys_created_on) IS NOT NULL
          AND COALESCE(opened_at, sys_created_on) <> ''
        GROUP BY period_month, category
        """
    )

    conn.execute("DROP VIEW IF EXISTS v_incident_reduction_snapshot")
    conn.execute(
        """
        CREATE VIEW v_incident_reduction_snapshot AS
        SELECT
            snapshot_date,
            COUNT(DISTINCT sys_id) AS incident_count,
            SUM(CASE WHEN closed_at IS NULL OR closed_at = '' THEN 1 ELSE 0 END) AS open_count,
            SUM(CASE WHEN closed_at IS NOT NULL AND closed_at <> '' THEN 1 ELSE 0 END) AS closed_count
        FROM sn_incident_snapshot
        GROUP BY snapshot_date
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

    conn.execute("DROP VIEW IF EXISTS v_incident_sla_adherence_monthly")
    conn.execute(
        """
        CREATE VIEW v_incident_sla_adherence_monthly AS
        WITH latest_snapshot AS (
            SELECT MAX(snapshot_date) AS snapshot_date
            FROM sn_incident_snapshot
        ),
        ranked AS (
            SELECT
                i.*,
                ROW_NUMBER() OVER (
                    PARTITION BY i.sys_id
                    ORDER BY i.snapshot_date DESC, i.pulled_at DESC
                ) AS rn
            FROM sn_incident_snapshot i
            JOIN latest_snapshot l ON i.snapshot_date = l.snapshot_date
        ),
        completed AS (
            SELECT
                substr(COALESCE(resolved_at, closed_at), 1, 7) AS period_month,
                COALESCE(NULLIF(priority, ''), 'Unknown') AS priority,
                (
                    JULIANDAY(COALESCE(resolved_at, closed_at))
                    - JULIANDAY(COALESCE(opened_at, sys_created_on))
                ) * 24.0 AS resolution_hours
            FROM ranked
            WHERE rn = 1
              AND COALESCE(opened_at, sys_created_on) IS NOT NULL
              AND COALESCE(resolved_at, closed_at) IS NOT NULL
        )
        SELECT
            period_month,
            priority,
            COUNT(*) AS completed_count,
            SUM(
                CASE
                    WHEN resolution_hours <= CASE priority
                        WHEN '1' THEN 4
                        WHEN '2' THEN 8
                        WHEN '3' THEN 24
                        WHEN '4' THEN 72
                        ELSE 120
                    END THEN 1
                    ELSE 0
                END
            ) AS within_sla_count,
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
            ) AS breached_count,
            ROUND(
                100.0 * SUM(
                    CASE
                        WHEN resolution_hours <= CASE priority
                            WHEN '1' THEN 4
                            WHEN '2' THEN 8
                            WHEN '3' THEN 24
                            WHEN '4' THEN 72
                            ELSE 120
                        END THEN 1
                        ELSE 0
                    END
                ) / NULLIF(COUNT(*), 0),
                2
            ) AS adherence_pct
        FROM completed
        GROUP BY period_month, priority
        """
    )

    conn.execute("DROP VIEW IF EXISTS v_incident_sla_adherence_snapshot")
    conn.execute(
        """
        CREATE VIEW v_incident_sla_adherence_snapshot AS
        WITH completed AS (
            SELECT
                snapshot_date,
                (
                    JULIANDAY(COALESCE(resolved_at, closed_at))
                    - JULIANDAY(COALESCE(opened_at, sys_created_on))
                ) * 24.0 AS resolution_hours,
                priority
            FROM sn_incident_snapshot
            WHERE COALESCE(opened_at, sys_created_on) IS NOT NULL
              AND COALESCE(resolved_at, closed_at) IS NOT NULL
        )
        SELECT
            snapshot_date,
            COUNT(*) AS completed_count,
            SUM(
                CASE
                    WHEN resolution_hours <= CASE priority
                        WHEN '1' THEN 4 WHEN '2' THEN 8 WHEN '3' THEN 24
                        WHEN '4' THEN 72 ELSE 120
                    END THEN 1 ELSE 0
                END
            ) AS within_sla_count,
            SUM(
                CASE
                    WHEN resolution_hours > CASE priority
                        WHEN '1' THEN 4 WHEN '2' THEN 8 WHEN '3' THEN 24
                        WHEN '4' THEN 72 ELSE 120
                    END THEN 1 ELSE 0
                END
            ) AS breached_count
        FROM completed
        GROUP BY snapshot_date
        """
    )

    conn.execute("DROP VIEW IF EXISTS v_aged_ticket_backlog_latest")
    conn.execute(
        """
        CREATE VIEW v_aged_ticket_backlog_latest AS
        WITH latest_incidents AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY sys_id ORDER BY snapshot_date DESC, pulled_at DESC
            ) AS rn
            FROM sn_incident_snapshot
        ),
        latest_requests AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY sys_id ORDER BY snapshot_date DESC, pulled_at DESC
            ) AS rn
            FROM sn_request_item_snapshot
        ),
        latest_changes AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY sys_id ORDER BY snapshot_date DESC, pulled_at DESC
            ) AS rn
            FROM sn_change_request_snapshot
        ),
        latest_problems AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY sys_id ORDER BY snapshot_date DESC, pulled_at DESC
            ) AS rn
            FROM sn_problem_snapshot
        )
        SELECT
            'Incident' AS ticket_type,
            number,
            snapshot_date,
            assignment_group,
            assigned_to,
            category,
            opened_at,
            ROUND(JULIANDAY(snapshot_date) - JULIANDAY(COALESCE(opened_at, sys_created_on)), 1) AS age_days,
            CASE WHEN closed_at IS NULL OR closed_at = '' THEN 1 ELSE 0 END AS is_open
        FROM latest_incidents
        WHERE rn = 1
        UNION ALL
        SELECT
            'Request Item', number, snapshot_date, assignment_group, assigned_to,
            cat_item, opened_at,
            ROUND(JULIANDAY(snapshot_date) - JULIANDAY(COALESCE(opened_at, sys_created_on)), 1),
            CASE WHEN closed_at IS NULL OR closed_at = '' THEN 1 ELSE 0 END
        FROM latest_requests
        WHERE rn = 1
        UNION ALL
        SELECT
            'Change Request', number, snapshot_date, assignment_group, assigned_to,
            type, opened_at,
            ROUND(JULIANDAY(snapshot_date) - JULIANDAY(COALESCE(opened_at, sys_created_on)), 1),
            CASE WHEN LOWER(COALESCE(state, '')) IN ('closed', 'complete', 'completed', 'cancelled', 'canceled') THEN 0 ELSE 1 END
        FROM latest_changes
        WHERE rn = 1
        UNION ALL
        SELECT
            'Problem', number, snapshot_date, assignment_group, assigned_to,
            category, opened_at,
            ROUND(JULIANDAY(snapshot_date) - JULIANDAY(COALESCE(opened_at, sys_created_on)), 1),
            CASE WHEN closed_at IS NULL OR closed_at = '' THEN 1 ELSE 0 END
        FROM latest_problems
        WHERE rn = 1
        """
    )

    conn.execute("DROP VIEW IF EXISTS v_ticket_backlog_monthly")
    conn.execute(
        """
        CREATE VIEW v_ticket_backlog_monthly AS
        WITH ticket_states AS (
            SELECT
                ticket_type,
                number,
                snapshot_date,
                assignment_group,
                assigned_to,
                opened_at,
                age_days,
                is_open,
                CASE
                    WHEN age_days >= 90 THEN '90+ days'
                    WHEN age_days >= 61 THEN '61-90 days'
                    WHEN age_days >= 31 THEN '31-60 days'
                    WHEN age_days >= 15 THEN '15-30 days'
                    ELSE '0-14 days'
                END AS age_band
            FROM v_aged_ticket_backlog_latest
        )
        SELECT
            snapshot_date,
            ticket_type,
            COALESCE(NULLIF(assignment_group, ''), 'Unassigned Group') AS assignment_group,
            COALESCE(NULLIF(assigned_to, ''), 'Unassigned') AS assigned_to,
            age_band,
            COUNT(*) AS ticket_count,
            SUM(is_open) AS open_count,
            SUM(CASE WHEN is_open = 1 AND age_days >= 30 THEN 1 ELSE 0 END) AS aged_open_count,
            ROUND(AVG(CASE WHEN is_open = 1 THEN age_days END), 1) AS avg_open_age_days,
            MAX(CASE WHEN is_open = 1 THEN age_days ELSE NULL END) AS oldest_open_age_days
        FROM ticket_states
        GROUP BY snapshot_date, ticket_type, assignment_group, assigned_to, age_band
        """
    )

    conn.execute("DROP VIEW IF EXISTS v_ticket_backlog_snapshot")
    conn.execute(
        """
        CREATE VIEW v_ticket_backlog_snapshot AS
        SELECT
            snapshot_date,
            'Incident' AS ticket_type,
            COUNT(*) AS open_count,
            SUM(
                CASE
                    WHEN JULIANDAY(snapshot_date) - JULIANDAY(COALESCE(opened_at, sys_created_on)) >= 30
                    THEN 1 ELSE 0
                END
            ) AS aged_open_count
        FROM sn_incident_snapshot
          WHERE LOWER(COALESCE(active, '')) IN ('true', '1', 'yes')
              OR (active IS NULL OR active = '') AND (closed_at IS NULL OR closed_at = '')
        GROUP BY snapshot_date
        UNION ALL
        SELECT
            snapshot_date,
            'Request Item',
            COUNT(*),
            SUM(CASE WHEN JULIANDAY(snapshot_date) - JULIANDAY(COALESCE(opened_at, sys_created_on)) >= 30 THEN 1 ELSE 0 END)
        FROM sn_request_item_snapshot
          WHERE LOWER(COALESCE(active, '')) IN ('true', '1', 'yes')
              OR (active IS NULL OR active = '') AND (closed_at IS NULL OR closed_at = '')
        GROUP BY snapshot_date
        UNION ALL
        SELECT
            snapshot_date,
            'Change Request',
            COUNT(*),
            SUM(CASE WHEN JULIANDAY(snapshot_date) - JULIANDAY(COALESCE(opened_at, sys_created_on)) >= 30 THEN 1 ELSE 0 END)
        FROM sn_change_request_snapshot
        WHERE LOWER(COALESCE(state, '')) NOT IN ('closed', 'complete', 'completed', 'cancelled', 'canceled')
        GROUP BY snapshot_date
        UNION ALL
        SELECT
            snapshot_date,
            'Problem',
            COUNT(*),
            SUM(CASE WHEN JULIANDAY(snapshot_date) - JULIANDAY(COALESCE(opened_at, sys_created_on)) >= 30 THEN 1 ELSE 0 END)
        FROM sn_problem_snapshot
        WHERE closed_at IS NULL OR closed_at = ''
        GROUP BY snapshot_date
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

    conn.execute("DROP VIEW IF EXISTS v_problem_region_territory_trends_daily")
    conn.execute(
        """
        CREATE VIEW v_problem_region_territory_trends_daily AS
        SELECT
            snapshot_date,
            COALESCE(NULLIF(cmdb_ci_region, ''), 'Unknown Region') AS region,
            COALESCE(NULLIF(cmdb_ci_territory, ''), 'Unknown Territory') AS territory,
            COALESCE(NULLIF(state, ''), 'Unknown') AS state,
            COUNT(*) AS problem_count,
            SUM(CASE WHEN closed_at IS NULL OR closed_at = '' THEN 1 ELSE 0 END) AS open_count,
            SUM(CASE WHEN closed_at IS NOT NULL AND closed_at <> '' THEN 1 ELSE 0 END) AS closed_count
        FROM sn_problem_snapshot
        GROUP BY snapshot_date, region, territory, state
        """
    )

    conn.execute("DROP VIEW IF EXISTS v_problem_region_normalized_daily")
    conn.execute(
        """
        CREATE VIEW v_problem_region_normalized_daily AS
        WITH normalized AS (
            SELECT
                snapshot_date,
                CASE
                    WHEN cmdb_ci_region IS NULL OR TRIM(cmdb_ci_region) = '' THEN 'Unknown Region'
                    WHEN LOWER(TRIM(cmdb_ci_region)) IN ('us', 'usa', 'united states', 'united states of america') THEN 'United States'
                    WHEN LOWER(TRIM(cmdb_ci_region)) IN (
                        'emea',
                        'uk and ireland',
                        'nordics',
                        'northern europe',
                        'south west europe',
                        'south, west and east europe',
                        'middle east, turkey and africa',
                        'middle east and africa',
                        'middle east',
                        'africa'
                    ) THEN 'EMEA'
                    WHEN LOWER(TRIM(cmdb_ci_region)) IN ('latin america', 'latam') THEN 'Latin America'
                    WHEN LOWER(TRIM(cmdb_ci_region)) IN ('asia pacific', 'apac', 'asean', 'anz') THEN 'APAC'
                    ELSE TRIM(cmdb_ci_region)
                END AS normalized_region,
                COALESCE(opened_at, sys_created_on) AS opened_at,
                closed_at,
                state
            FROM sn_problem_snapshot
        )
        SELECT
            snapshot_date,
            normalized_region,
            COUNT(*) AS problem_count,
            SUM(CASE WHEN closed_at IS NULL OR closed_at = '' THEN 1 ELSE 0 END) AS open_count,
            SUM(
                CASE
                    WHEN (closed_at IS NULL OR closed_at = '')
                     AND JULIANDAY(snapshot_date) - JULIANDAY(opened_at) >= 30
                    THEN 1 ELSE 0
                END
            ) AS aged_open_count
        FROM normalized
        GROUP BY snapshot_date, normalized_region
        """
    )

    conn.execute("DROP VIEW IF EXISTS v_problem_goal_region_snapshot")
    conn.execute(
        """
        CREATE VIEW v_problem_goal_region_snapshot AS
        SELECT
            snapshot_date,
            normalized_region AS region,
            problem_count,
            open_count,
            aged_open_count,
            ROUND(100.0 * aged_open_count / NULLIF(open_count, 0), 2) AS aged_open_pct
        FROM v_problem_region_normalized_daily
        """
    )

    conn.execute("DROP VIEW IF EXISTS v_problem_territory_normalized_daily")
    conn.execute(
        """
        CREATE VIEW v_problem_territory_normalized_daily AS
        WITH normalized AS (
            SELECT
                snapshot_date,
                CASE
                    WHEN cmdb_ci_territory IS NULL OR TRIM(cmdb_ci_territory) = '' THEN 'Unknown Territory'
                    ELSE TRIM(cmdb_ci_territory)
                END AS normalized_territory,
                COALESCE(opened_at, sys_created_on) AS opened_at,
                closed_at
            FROM sn_problem_snapshot
        )
        SELECT
            snapshot_date,
            normalized_territory,
            COUNT(*) AS problem_count,
            SUM(CASE WHEN closed_at IS NULL OR closed_at = '' THEN 1 ELSE 0 END) AS open_count,
            SUM(
                CASE
                    WHEN (closed_at IS NULL OR closed_at = '')
                     AND JULIANDAY(snapshot_date) - JULIANDAY(opened_at) >= 30
                    THEN 1 ELSE 0
                END
            ) AS aged_open_count
        FROM normalized
        GROUP BY snapshot_date, normalized_territory
        """
    )

    conn.execute("DROP VIEW IF EXISTS v_problem_goal_territory_snapshot")
    conn.execute(
        """
        CREATE VIEW v_problem_goal_territory_snapshot AS
        SELECT
            snapshot_date,
            normalized_territory AS territory,
            problem_count,
            open_count,
            aged_open_count,
            ROUND(100.0 * aged_open_count / NULLIF(open_count, 0), 2) AS aged_open_pct
        FROM v_problem_territory_normalized_daily
        """
    )

    conn.execute("DROP VIEW IF EXISTS v_problem_ci_lob_bridge_latest")
    conn.execute(
        """
        CREATE VIEW v_problem_ci_lob_bridge_latest AS
        WITH latest AS (
            SELECT MAX(snapshot_date) AS snapshot_date FROM sn_problem_snapshot
        )
        SELECT
            p.snapshot_date,
            p.cmdb_ci_sys_id,
            p.cmdb_ci_name,
            p.cmdb_ci_lob_sys_id,
            p.cmdb_ci_lob,
            p.cmdb_ci_customer_relationship_sys_id AS cmdb_ci_customer_account_sys_id,
            p.cmdb_ci_customer_relationship AS cmdb_ci_customer_account,
            COALESCE(NULLIF(p.cmdb_ci_customer_relationship_region, ''), 'Unknown') AS customer_country,
            COALESCE(NULLIF(p.cmdb_ci_customer_relationship_territory, ''), 'Unknown') AS customer_state,
            COALESCE(NULLIF(p.cmdb_ci_region, ''), 'Unknown Region') AS region,
            COALESCE(NULLIF(p.cmdb_ci_territory, ''), 'Unknown Territory') AS territory,
            COUNT(*) AS problem_count
        FROM sn_problem_snapshot p
        JOIN latest l ON p.snapshot_date = l.snapshot_date
        GROUP BY
            p.snapshot_date,
            p.cmdb_ci_sys_id,
            p.cmdb_ci_name,
            p.cmdb_ci_lob_sys_id,
            p.cmdb_ci_lob,
            p.cmdb_ci_customer_relationship_sys_id,
            p.cmdb_ci_customer_relationship,
            customer_country,
            customer_state,
            region,
            territory
        ORDER BY problem_count DESC, p.cmdb_ci_name
        """
    )

    conn.execute("DROP VIEW IF EXISTS v_problem_customer_relationship_latest")
    conn.execute(
        """
        CREATE VIEW v_problem_customer_relationship_latest AS
        WITH latest AS (
            SELECT MAX(snapshot_date) AS snapshot_date FROM sn_problem_snapshot
        )
        SELECT
            p.snapshot_date,
            p.cmdb_ci_customer_relationship_sys_id AS cmdb_ci_customer_account_sys_id,
            COALESCE(NULLIF(p.cmdb_ci_customer_relationship, ''), 'Unknown Customer Account') AS customer_account,
            COALESCE(NULLIF(p.cmdb_ci_customer_relationship_region, ''), 'Unknown') AS customer_country,
            COALESCE(NULLIF(p.cmdb_ci_customer_relationship_territory, ''), 'Unknown') AS customer_state,
            p.cmdb_ci_lob_sys_id,
            COALESCE(NULLIF(p.cmdb_ci_lob, ''), 'Unknown LOB') AS lob,
            COALESCE(NULLIF(p.cmdb_ci_region, ''), 'Unknown Region') AS region,
            COALESCE(NULLIF(p.cmdb_ci_territory, ''), 'Unknown Territory') AS territory,
            COUNT(*) AS problem_count,
            COUNT(DISTINCT p.cmdb_ci_sys_id) AS ci_count
        FROM sn_problem_snapshot p
        JOIN latest l ON p.snapshot_date = l.snapshot_date
        GROUP BY
            p.snapshot_date,
            p.cmdb_ci_customer_relationship_sys_id,
            customer_account,
            customer_country,
            customer_state,
            p.cmdb_ci_lob_sys_id,
            lob,
            region,
            territory
        ORDER BY problem_count DESC, customer_account
        """
    )

    conn.execute("DROP VIEW IF EXISTS v_problem_customer_country_trends_daily")
    conn.execute(
        """
        CREATE VIEW v_problem_customer_country_trends_daily AS
        SELECT
            snapshot_date,
            COALESCE(NULLIF(cmdb_ci_customer_relationship_region, ''), 'Unknown') AS customer_country,
            COALESCE(NULLIF(cmdb_ci_customer_relationship_territory, ''), 'Unknown') AS customer_state,
            COUNT(*) AS problem_count,
            SUM(CASE WHEN closed_at IS NULL OR closed_at = '' THEN 1 ELSE 0 END) AS open_count,
            SUM(CASE WHEN closed_at IS NOT NULL AND closed_at <> '' THEN 1 ELSE 0 END) AS closed_count
        FROM sn_problem_snapshot
        GROUP BY snapshot_date, customer_country, customer_state
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

    # Each sync run represents a full table pull for included datasets.
    # Replace same-day rows so repeated runs do not accumulate stale records.
    conn.execute("DELETE FROM sn_incident_snapshot WHERE snapshot_date = ?", (snapshot_date,))
    conn.execute("DELETE FROM sn_request_item_snapshot WHERE snapshot_date = ?", (snapshot_date,))
    if change_requests:
        conn.execute("DELETE FROM sn_change_request_snapshot WHERE snapshot_date = ?", (snapshot_date,))
    if problems:
        conn.execute("DELETE FROM sn_problem_snapshot WHERE snapshot_date = ?", (snapshot_date,))

    for row in incidents:
        conn.execute(
            """
            INSERT OR REPLACE INTO sn_incident_snapshot (
                run_id, snapshot_date, pulled_at, sys_id, number,
                opened_at, sys_created_on, sys_updated_on, resolved_at, closed_at,
                active, state, priority, severity, impact, urgency,
                category, subcategory, assignment_group, assigned_to, caller_id,
                short_description, raw_payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                snapshot_date,
                pulled_at,
                str(row.get("sys_id", "")),
                _to_db_value(row.get("number")),
                _to_db_value(row.get("opened_at")),
                _to_db_value(row.get("sys_created_on")),
                _to_db_value(row.get("sys_updated_on")),
                _to_db_value(row.get("resolved_at")),
                _to_db_value(row.get("closed_at")),
                _to_db_value(row.get("active")),
                _to_db_value(row.get("state")),
                _to_db_value(row.get("priority")),
                _to_db_value(row.get("severity")),
                _to_db_value(row.get("impact")),
                _to_db_value(row.get("urgency")),
                _to_db_value(row.get("category")),
                _to_db_value(row.get("subcategory")),
                _to_db_value(row.get("assignment_group")),
                _to_db_value(row.get("assigned_to")),
                _to_db_value(row.get("caller_id")),
                _to_db_value(row.get("short_description")),
                json.dumps(row, default=str),
            ),
        )

    for row in request_items:
        conn.execute(
            """
            INSERT OR REPLACE INTO sn_request_item_snapshot (
                run_id, snapshot_date, pulled_at, sys_id, number,
                request, opened_at, sys_created_on, sys_updated_on, closed_at,
                active, state, priority, cat_item, short_description, assignment_group,
                assigned_to, requested_for, raw_payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                snapshot_date,
                pulled_at,
                str(row.get("sys_id", "")),
                _to_db_value(row.get("number")),
                _to_db_value(row.get("request")),
                _to_db_value(row.get("opened_at")),
                _to_db_value(row.get("sys_created_on")),
                _to_db_value(row.get("sys_updated_on")),
                _to_db_value(row.get("closed_at")),
                _to_db_value(row.get("active")),
                _to_db_value(row.get("state")),
                _to_db_value(row.get("priority")),
                _to_db_value(row.get("cat_item")),
                _to_db_value(row.get("short_description")),
                _to_db_value(row.get("assignment_group")),
                _to_db_value(row.get("assigned_to")),
                _to_db_value(row.get("requested_for")),
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
                _to_db_value(row.get("number")),
                _to_db_value(row.get("opened_at")),
                _to_db_value(row.get("sys_created_on")),
                _to_db_value(row.get("sys_updated_on")),
                _to_db_value(row.get("start_date")),
                _to_db_value(row.get("end_date")),
                _to_db_value(row.get("state")),
                _to_db_value(row.get("type")),
                _to_db_value(row.get("risk")),
                _to_db_value(row.get("priority")),
                _to_db_value(row.get("category")),
                _to_db_value(row.get("assignment_group")),
                _to_db_value(row.get("assigned_to")),
                _to_db_value(row.get("short_description")),
                json.dumps(row, default=str),
            ),
        )

    for row in problems:
        conn.execute(
            """
            INSERT OR REPLACE INTO sn_problem_snapshot (
                run_id, snapshot_date, pulled_at, sys_id, number,
                opened_at, sys_created_on, sys_updated_on, closed_at, state,
                priority, known_error, category,
                cmdb_ci_sys_id, cmdb_ci_name,
                cmdb_ci_lob_sys_id, cmdb_ci_lob,
                cmdb_ci_lob_details_sys_id, cmdb_ci_lob_details,
                cmdb_ci_customer_relationship_sys_id, cmdb_ci_customer_relationship,
                cmdb_ci_customer_relationship_region, cmdb_ci_customer_relationship_territory,
                cmdb_ci_region, cmdb_ci_territory, cmdb_ci_region_source, cmdb_ci_territory_source,
                assignment_group,
                assigned_to, short_description, raw_payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                snapshot_date,
                pulled_at,
                str(row.get("sys_id", "")),
                _to_db_value(row.get("number")),
                _to_db_value(row.get("opened_at")),
                _to_db_value(row.get("sys_created_on")),
                _to_db_value(row.get("sys_updated_on")),
                _to_db_value(row.get("closed_at")),
                _to_db_value(row.get("state")),
                _to_db_value(row.get("priority")),
                _to_db_value(row.get("known_error")),
                _to_db_value(row.get("category")),
                _to_db_value(row.get("cmdb_ci_sys_id")),
                _to_db_value(row.get("cmdb_ci_name")),
                _to_db_value(row.get("cmdb_ci_lob_sys_id")),
                _to_db_value(row.get("cmdb_ci_lob")),
                _to_db_value(row.get("cmdb_ci_lob_details_sys_id")),
                _to_db_value(row.get("cmdb_ci_lob_details")),
                _to_db_value(row.get("cmdb_ci_customer_relationship_sys_id")),
                _to_db_value(row.get("cmdb_ci_customer_relationship")),
                _to_db_value(row.get("cmdb_ci_customer_relationship_region")),
                _to_db_value(row.get("cmdb_ci_customer_relationship_territory")),
                _to_db_value(row.get("cmdb_ci_region")),
                _to_db_value(row.get("cmdb_ci_territory")),
                _to_db_value(row.get("cmdb_ci_region_source")),
                _to_db_value(row.get("cmdb_ci_territory_source")),
                _to_db_value(row.get("assignment_group")),
                _to_db_value(row.get("assigned_to")),
                _to_db_value(row.get("short_description")),
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
