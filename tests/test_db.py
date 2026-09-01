"""Tests for db module."""

from db import (
    ensure_ingest_log,
    ensure_servicenow_schema,
    get_conn,
    get_tables,
    is_already_ingested,
    record_ingest,
    store_servicenow_snapshot,
)


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


def test_store_servicenow_snapshot_problem_ci_enrichment_fields(tmp_db):
    ensure_servicenow_schema(tmp_db)
    snapshot = {
        "snapshot_date": "2026-05-19",
        "pulled_at": "2026-05-19T00:00:00Z",
        "since_days": 365,
        "incidents": [],
        "request_items": [],
        "change_requests": [],
        "problems": [
            {
                "sys_id": "prob-1",
                "number": "PRB0001",
                "state": "Open",
                "priority": "2",
                "known_error": "false",
                "category": "Network",
                "cmdb_ci_sys_id": "ci-123",
                "cmdb_ci_name": "Edge Router",
                "cmdb_ci_lob_sys_id": "lob-1",
                "cmdb_ci_lob": "Hosting",
                "cmdb_ci_lob_details_sys_id": "lobd-1",
                "cmdb_ci_lob_details": "Hosting Core",
                "cmdb_ci_customer_relationship_sys_id": "cr-1",
                "cmdb_ci_customer_relationship": "Acme Corp",
                "cmdb_ci_region": "NA",
                "cmdb_ci_territory": "US-East",
                "short_description": "Packet loss",
            }
        ],
        "message": "test",
    }

    store_servicenow_snapshot(tmp_db, snapshot)
    row = tmp_db.execute(
        """
        SELECT
            cmdb_ci_sys_id,
            cmdb_ci_name,
            cmdb_ci_lob_sys_id,
            cmdb_ci_lob,
            cmdb_ci_lob_details_sys_id,
            cmdb_ci_lob_details,
            cmdb_ci_customer_relationship_sys_id,
            cmdb_ci_customer_relationship,
            cmdb_ci_region,
            cmdb_ci_territory
        FROM sn_problem_snapshot
        WHERE sys_id = 'prob-1'
        """
    ).fetchone()

    assert row["cmdb_ci_sys_id"] == "ci-123"
    assert row["cmdb_ci_name"] == "Edge Router"
    assert row["cmdb_ci_lob_sys_id"] == "lob-1"
    assert row["cmdb_ci_lob"] == "Hosting"
    assert row["cmdb_ci_lob_details_sys_id"] == "lobd-1"
    assert row["cmdb_ci_lob_details"] == "Hosting Core"
    assert row["cmdb_ci_customer_relationship_sys_id"] == "cr-1"
    assert row["cmdb_ci_customer_relationship"] == "Acme Corp"
    assert row["cmdb_ci_region"] == "NA"
    assert row["cmdb_ci_territory"] == "US-East"


def test_store_servicenow_snapshot_normalizes_empty_service_now_values(tmp_db):
    ensure_servicenow_schema(tmp_db)
    snapshot = {
        "snapshot_date": "2026-05-19",
        "pulled_at": "2026-05-19T00:00:00Z",
        "since_days": 365,
        "incidents": [
            {
                "sys_id": "inc-open",
                "number": "INC0001",
                "active": "true",
                "state": "Active",
                "resolved_at": {"display_value": "", "value": ""},
                "closed_at": {"display_value": "", "value": ""},
            }
        ],
        "request_items": [],
        "change_requests": [],
        "problems": [],
    }

    store_servicenow_snapshot(tmp_db, snapshot)
    row = tmp_db.execute(
        "SELECT resolved_at, closed_at FROM sn_incident_snapshot WHERE sys_id = 'inc-open'"
    ).fetchone()

    assert row["resolved_at"] is None
    assert row["closed_at"] is None


def test_problem_region_territory_trend_view_aggregates(tmp_db):
    ensure_servicenow_schema(tmp_db)
    snapshot = {
        "snapshot_date": "2026-05-19",
        "pulled_at": "2026-05-19T00:00:00Z",
        "since_days": 365,
        "incidents": [],
        "request_items": [],
        "change_requests": [],
        "problems": [
            {
                "sys_id": "prob-open",
                "number": "PRB0100",
                "state": "Open",
                "closed_at": None,
                "cmdb_ci_region": "NA",
                "cmdb_ci_territory": "US-East",
            },
            {
                "sys_id": "prob-closed",
                "number": "PRB0101",
                "state": "Closed",
                "closed_at": "2026-05-18 12:00:00",
                "cmdb_ci_region": "NA",
                "cmdb_ci_territory": "US-East",
            },
        ],
        "message": "trend test",
    }

    store_servicenow_snapshot(tmp_db, snapshot)
    rows = tmp_db.execute(
        """
        SELECT state, problem_count, open_count, closed_count
        FROM v_problem_region_territory_trends_daily
        WHERE snapshot_date = '2026-05-19'
          AND region = 'NA'
          AND territory = 'US-East'
        ORDER BY state
        """
    ).fetchall()

    assert len(rows) == 2
    by_state = {row["state"]: row for row in rows}
    assert by_state["Open"]["problem_count"] == 1
    assert by_state["Open"]["open_count"] == 1
    assert by_state["Open"]["closed_count"] == 0
    assert by_state["Closed"]["problem_count"] == 1
    assert by_state["Closed"]["open_count"] == 0
    assert by_state["Closed"]["closed_count"] == 1


def test_incident_reduction_view_counts_latest_distinct_incidents(tmp_db):
    ensure_servicenow_schema(tmp_db)
    snapshot = {
        "snapshot_date": "2026-08-21",
        "pulled_at": "2026-08-21T00:00:00Z",
        "since_days": 365,
        "incidents": [
            {
                "sys_id": "inc-1",
                "number": "INC0001",
                "opened_at": "2026-01-10 10:00:00",
                "sys_created_on": "2026-01-10 10:00:00",
                "closed_at": None,
                "category": "Network",
            },
            {
                "sys_id": "inc-2",
                "number": "INC0002",
                "opened_at": "2026-01-20 10:00:00",
                "sys_created_on": "2026-01-20 10:00:00",
                "closed_at": "2026-01-21 10:00:00",
                "category": "Network",
            },
        ],
        "request_items": [],
        "change_requests": [],
        "problems": [],
        "message": "reduction view test",
    }

    store_servicenow_snapshot(tmp_db, snapshot)
    row = tmp_db.execute(
        """
        SELECT incident_count, open_count, closed_count
        FROM v_incident_reduction_monthly
        WHERE period_month = '2026-01' AND category = 'Network'
        """
    ).fetchone()

    assert row["incident_count"] == 2
    assert row["open_count"] == 1
    assert row["closed_count"] == 1


def test_sla_adherence_and_aged_backlog_views(tmp_db):
    ensure_servicenow_schema(tmp_db)
    snapshot = {
        "snapshot_date": "2026-08-21",
        "pulled_at": "2026-08-21T00:00:00Z",
        "since_days": 365,
        "incidents": [
            {
                "sys_id": "inc-sla-good",
                "number": "INC0100",
                "opened_at": "2026-08-20 08:00:00",
                "resolved_at": "2026-08-20 10:00:00",
                "closed_at": "2026-08-20 10:00:00",
                "priority": "1",
                "assignment_group": "CTL",
                "assigned_to": "Engineer A",
                "category": "Network",
            },
            {
                "sys_id": "inc-aged",
                "number": "INC0101",
                "opened_at": "2026-08-01 08:00:00",
                "priority": "2",
                "assignment_group": "CTL",
                "assigned_to": "Engineer A",
                "category": "Access",
            },
        ],
        "request_items": [],
        "change_requests": [],
        "problems": [],
        "message": "SLA and backlog test",
    }

    store_servicenow_snapshot(tmp_db, snapshot)
    sla = tmp_db.execute(
        """
        SELECT completed_count, within_sla_count, breached_count, adherence_pct
        FROM v_incident_sla_adherence_monthly
        WHERE period_month = '2026-08' AND priority = '1'
        """
    ).fetchone()
    backlog = tmp_db.execute(
        """
        SELECT ticket_type, number, assignment_group, is_open
        FROM v_aged_ticket_backlog_latest
        WHERE number = 'INC0101'
        """
    ).fetchone()

    assert sla["completed_count"] == 1
    assert sla["within_sla_count"] == 1
    assert sla["breached_count"] == 0
    assert sla["adherence_pct"] == 100.0
    assert backlog["ticket_type"] == "Incident"
    assert backlog["assignment_group"] == "CTL"
    assert backlog["is_open"] == 1


def test_ticket_backlog_monthly_separates_ticket_types_and_age_bands(tmp_db):
    ensure_servicenow_schema(tmp_db)
    snapshot = {
        "snapshot_date": "2026-08-21",
        "pulled_at": "2026-08-21T00:00:00Z",
        "since_days": 365,
        "incidents": [
            {
                "sys_id": "inc-aged",
                "number": "INC0200",
                "opened_at": "2026-07-01 08:00:00",
                "assignment_group": "CTL",
                "assigned_to": "Engineer A",
            }
        ],
        "request_items": [
            {
                "sys_id": "ritm-new",
                "number": "RITM0200",
                "opened_at": "2026-08-20 08:00:00",
                "assignment_group": "CTL",
                "assigned_to": "Engineer A",
            }
        ],
        "change_requests": [],
        "problems": [],
        "message": "backlog summary test",
    }

    store_servicenow_snapshot(tmp_db, snapshot)
    rows = tmp_db.execute(
        """
        SELECT ticket_type, age_band, open_count, aged_open_count
        FROM v_ticket_backlog_monthly
        WHERE snapshot_date = '2026-08-21'
        ORDER BY ticket_type
        """
    ).fetchall()

    assert [row["ticket_type"] for row in rows] == ["Incident", "Request Item"]
    assert rows[0]["age_band"] == "31-60 days"
    assert rows[0]["open_count"] == 1
    assert rows[0]["aged_open_count"] == 1
    assert rows[1]["age_band"] == "0-14 days"
    assert rows[1]["open_count"] == 1
    assert rows[1]["aged_open_count"] == 0


def test_snapshot_goal_views_support_baseline_comparison(tmp_db):
    ensure_servicenow_schema(tmp_db)
    for snapshot_date, incident_count in (("2026-05-19", 10), ("2026-08-21", 8)):
        incidents = [
            {
                "sys_id": f"inc-{snapshot_date}-{index}",
                "number": f"INC{index:04d}",
                "opened_at": f"{snapshot_date} 08:00:00",
                "closed_at": None,
                "priority": "1",
            }
            for index in range(incident_count)
        ]
        snapshot = {
            "snapshot_date": snapshot_date,
            "pulled_at": f"{snapshot_date}T00:00:00Z",
            "since_days": 365,
            "incidents": incidents,
            "request_items": [],
            "change_requests": [],
            "problems": [],
            "message": "snapshot comparison test",
        }
        store_servicenow_snapshot(tmp_db, snapshot)

    incident_rows = tmp_db.execute(
        "SELECT snapshot_date, incident_count FROM v_incident_reduction_snapshot ORDER BY snapshot_date"
    ).fetchall()
    backlog_rows = tmp_db.execute(
        "SELECT snapshot_date, ticket_type, aged_open_count FROM v_ticket_backlog_snapshot WHERE ticket_type = 'Incident' ORDER BY snapshot_date"
    ).fetchall()

    assert [(row["snapshot_date"], row["incident_count"]) for row in incident_rows] == [
        ("2026-05-19", 10),
        ("2026-08-21", 8),
    ]
    assert [(row["snapshot_date"], row["aged_open_count"]) for row in backlog_rows] == [
        ("2026-05-19", 0),
        ("2026-08-21", 0),
    ]


def test_active_status_overrides_stale_closed_at_for_backlog(tmp_db):
    ensure_servicenow_schema(tmp_db)
    store_servicenow_snapshot(
        tmp_db,
        {
            "snapshot_date": "2026-08-21",
            "pulled_at": "2026-08-21T00:00:00Z",
            "since_days": 365,
            "incidents": [
                {
                    "sys_id": "active-inc-1",
                    "number": "INC0001",
                    "opened_at": "2026-06-01 08:00:00",
                    "closed_at": "2026-06-02 08:00:00",
                    "active": "true",
                }
            ],
            "request_items": [],
            "change_requests": [],
            "problems": [],
        },
    )

    row = tmp_db.execute(
        "SELECT open_count, aged_open_count FROM v_ticket_backlog_snapshot WHERE snapshot_date = '2026-08-21' AND ticket_type = 'Incident'"
    ).fetchone()

    assert (row["open_count"], row["aged_open_count"]) == (1, 1)


def test_problem_goal_region_snapshot_normalizes_region_aliases(tmp_db):
    ensure_servicenow_schema(tmp_db)
    store_servicenow_snapshot(
        tmp_db,
        {
            "snapshot_date": "2026-08-21",
            "pulled_at": "2026-08-21T00:00:00Z",
            "since_days": 365,
            "incidents": [],
            "request_items": [],
            "change_requests": [],
            "problems": [
                {
                    "sys_id": "prb-us-1",
                    "number": "PRB0001",
                    "opened_at": "2026-07-01 08:00:00",
                    "closed_at": None,
                    "cmdb_ci_region": "US",
                },
                {
                    "sys_id": "prb-us-2",
                    "number": "PRB0002",
                    "opened_at": "2026-07-02 08:00:00",
                    "closed_at": None,
                    "cmdb_ci_region": "United States",
                },
                {
                    "sys_id": "prb-emea-1",
                    "number": "PRB0003",
                    "opened_at": "2026-07-03 08:00:00",
                    "closed_at": None,
                    "cmdb_ci_region": "UK and Ireland",
                },
            ],
        },
    )

    rows = tmp_db.execute(
        "SELECT region, open_count, aged_open_count FROM v_problem_goal_region_snapshot WHERE snapshot_date = '2026-08-21' ORDER BY region"
    ).fetchall()

    assert [(row["region"], row["open_count"], row["aged_open_count"]) for row in rows] == [
        ("EMEA", 1, 1),
        ("United States", 2, 2),
    ]


def test_problem_goal_territory_snapshot_uses_customer_relationship_territory(tmp_db):
    ensure_servicenow_schema(tmp_db)
    store_servicenow_snapshot(
        tmp_db,
        {
            "snapshot_date": "2026-08-21",
            "pulled_at": "2026-08-21T00:00:00Z",
            "since_days": 365,
            "incidents": [],
            "request_items": [],
            "change_requests": [],
            "problems": [
                {
                    "sys_id": "prb-ter-1",
                    "number": "PRB1001",
                    "opened_at": "2026-07-01 08:00:00",
                    "closed_at": None,
                    "cmdb_ci_territory": "US-East",
                },
                {
                    "sys_id": "prb-ter-2",
                    "number": "PRB1002",
                    "opened_at": "2026-07-10 08:00:00",
                    "closed_at": None,
                    "cmdb_ci_territory": "",
                },
            ],
        },
    )

    rows = tmp_db.execute(
        "SELECT territory, open_count, aged_open_count FROM v_problem_goal_territory_snapshot WHERE snapshot_date = '2026-08-21' ORDER BY territory"
    ).fetchall()

    assert [(row["territory"], row["open_count"], row["aged_open_count"]) for row in rows] == [
        ("US-East", 1, 1),
        ("Unknown Territory", 1, 1),
    ]
