"""Export problem ticket report datasets to Excel workbook."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from db import DB_PATH

OUTPUT_PATH = "powerbi/problem_report_export.xlsx"

QUERIES: list[tuple[str, str]] = [
    (
        "Problem Table",
        """
        WITH latest AS (
            SELECT MAX(snapshot_date) AS snapshot_date FROM sn_problem_snapshot
        )
        SELECT
            p.snapshot_date,
            p.number,
            p.opened_at,
            p.sys_created_on,
            p.sys_updated_on,
            p.closed_at,
            p.state,
            p.priority,
            p.known_error,
            p.category,
            p.cmdb_ci_name,
            p.cmdb_ci_lob,
            p.cmdb_ci_lob_details,
            p.cmdb_ci_customer_relationship,
            COALESCE(NULLIF(p.cmdb_ci_region, ''), 'Unknown Region') AS region,
            COALESCE(NULLIF(p.cmdb_ci_territory, ''), 'Unknown Territory') AS territory,
            p.assignment_group,
            p.assigned_to,
            p.short_description,
            p.sys_id,
            p.cmdb_ci_sys_id
        FROM sn_problem_snapshot p
        JOIN latest l ON p.snapshot_date = l.snapshot_date
        ORDER BY p.opened_at DESC
        """,
    ),
    (
        "CI LOB Bridge",
        """
        SELECT
            snapshot_date,
            cmdb_ci_sys_id,
            cmdb_ci_name,
            cmdb_ci_lob_sys_id,
            cmdb_ci_lob,
            cmdb_ci_lob_details_sys_id,
            cmdb_ci_lob_details,
            cmdb_ci_customer_relationship_sys_id,
            cmdb_ci_customer_relationship,
            region,
            territory,
            problem_count
        FROM v_problem_ci_lob_bridge_latest
        ORDER BY problem_count DESC, cmdb_ci_name
        """,
    ),
    (
        "Customer Rel Bridge",
        """
        SELECT
            snapshot_date,
            cmdb_ci_customer_relationship_sys_id,
            customer_relationship,
            cmdb_ci_lob_sys_id,
            lob,
            region,
            territory,
            problem_count,
            ci_count
        FROM v_problem_customer_relationship_latest
        ORDER BY problem_count DESC, customer_relationship
        """,
    ),
    (
        "Daily Trend",
        """
        SELECT
            snapshot_date,
            SUM(problem_count) AS total_problems,
            SUM(open_count) AS open_problems,
            SUM(closed_count) AS closed_problems
        FROM v_problem_region_territory_trends_daily
        GROUP BY snapshot_date
        ORDER BY snapshot_date
        """,
    ),
    (
        "Region Trend",
        """
        SELECT
            snapshot_date,
            region,
            SUM(problem_count) AS total_problems,
            SUM(open_count) AS open_problems,
            SUM(closed_count) AS closed_problems
        FROM v_problem_region_territory_trends_daily
        GROUP BY snapshot_date, region
        ORDER BY snapshot_date, region
        """,
    ),
    (
        "Territory Trend",
        """
        SELECT
            snapshot_date,
            region,
            territory,
            SUM(problem_count) AS total_problems,
            SUM(open_count) AS open_problems,
            SUM(closed_count) AS closed_problems
        FROM v_problem_region_territory_trends_daily
        GROUP BY snapshot_date, region, territory
        ORDER BY snapshot_date, region, territory
        """,
    ),
    (
        "Latest Detail",
        """
        WITH latest AS (
            SELECT MAX(snapshot_date) AS snapshot_date FROM sn_problem_snapshot
        )
        SELECT
            p.snapshot_date,
            p.sys_id,
            p.number,
            p.opened_at,
            p.sys_created_on,
            p.sys_updated_on,
            p.closed_at,
            p.state,
            p.priority,
            p.known_error,
            p.category,
            p.cmdb_ci_sys_id,
            p.cmdb_ci_name,
            COALESCE(NULLIF(p.cmdb_ci_region, ''), 'Unknown Region') AS region,
            COALESCE(NULLIF(p.cmdb_ci_territory, ''), 'Unknown Territory') AS territory,
            p.assignment_group,
            p.assigned_to,
            p.short_description,
            CASE WHEN p.closed_at IS NULL OR p.closed_at = '' THEN 1 ELSE 0 END AS is_open,
            CAST(
                JULIANDAY(COALESCE(p.closed_at, DATE('now')))
                - JULIANDAY(COALESCE(p.opened_at, p.sys_created_on))
                AS REAL
            ) AS age_days
        FROM sn_problem_snapshot p
        JOIN latest l ON p.snapshot_date = l.snapshot_date
        """,
    ),
    (
        "Category State",
        """
        WITH latest AS (
            SELECT MAX(snapshot_date) AS snapshot_date FROM sn_problem_snapshot
        )
        SELECT
            p.category,
            p.state,
            COUNT(*) AS problem_count,
            SUM(CASE WHEN p.closed_at IS NULL OR p.closed_at = '' THEN 1 ELSE 0 END) AS open_count,
            SUM(CASE WHEN p.closed_at IS NOT NULL AND p.closed_at <> '' THEN 1 ELSE 0 END) AS closed_count
        FROM sn_problem_snapshot p
        JOIN latest l ON p.snapshot_date = l.snapshot_date
        GROUP BY p.category, p.state
        ORDER BY problem_count DESC
        """,
    ),
    (
        "Region Territory Latest",
        """
        WITH latest AS (
            SELECT MAX(snapshot_date) AS snapshot_date FROM sn_problem_snapshot
        )
        SELECT
            COALESCE(NULLIF(p.cmdb_ci_region, ''), 'Unknown Region') AS region,
            COALESCE(NULLIF(p.cmdb_ci_territory, ''), 'Unknown Territory') AS territory,
            COUNT(*) AS problem_count,
            SUM(CASE WHEN p.closed_at IS NULL OR p.closed_at = '' THEN 1 ELSE 0 END) AS open_count,
            SUM(CASE WHEN p.closed_at IS NOT NULL AND p.closed_at <> '' THEN 1 ELSE 0 END) AS closed_count
        FROM sn_problem_snapshot p
        JOIN latest l ON p.snapshot_date = l.snapshot_date
        GROUP BY region, territory
        ORDER BY problem_count DESC
        """,
    ),
    (
        "Aging Bands",
        """
        WITH latest AS (
            SELECT MAX(snapshot_date) AS snapshot_date FROM sn_problem_snapshot
        ),
        base AS (
            SELECT
                CAST(
                    JULIANDAY(COALESCE(p.closed_at, DATE('now')))
                    - JULIANDAY(COALESCE(p.opened_at, p.sys_created_on))
                    AS REAL
                ) AS age_days,
                CASE WHEN p.closed_at IS NULL OR p.closed_at = '' THEN 1 ELSE 0 END AS is_open
            FROM sn_problem_snapshot p
            JOIN latest l ON p.snapshot_date = l.snapshot_date
        )
        SELECT
            CASE
                WHEN age_days < 7 THEN '0-6 days'
                WHEN age_days < 14 THEN '7-13 days'
                WHEN age_days < 30 THEN '14-29 days'
                WHEN age_days < 60 THEN '30-59 days'
                WHEN age_days < 90 THEN '60-89 days'
                ELSE '90+ days'
            END AS age_band,
            COUNT(*) AS problem_count,
            SUM(is_open) AS open_count
        FROM base
        GROUP BY age_band
        ORDER BY
            CASE age_band
                WHEN '0-6 days' THEN 1
                WHEN '7-13 days' THEN 2
                WHEN '14-29 days' THEN 3
                WHEN '30-59 days' THEN 4
                WHEN '60-89 days' THEN 5
                ELSE 6
            END
        """,
    ),
]


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    output = Path(OUTPUT_PATH)
    output.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_name, sql in QUERIES:
            df = pd.read_sql_query(sql.strip(), conn)
            df.to_excel(writer, sheet_name=sheet_name, index=False)
            print(f"  {sheet_name}: {len(df)} rows")

    conn.close()
    print(f"\nExported to {output.resolve()}")


if __name__ == "__main__":
    main()
