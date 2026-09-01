"""Streamlit app for ingesting ServiceNow data into SQLite and analyzing trends."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from datetime import timedelta

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from db import (
    DB_PATH,
    drop_table,
    ensure_servicenow_schema,
    get_conn,
    get_tables,
    store_servicenow_snapshot,
)
from servicenow import get_azure_access_token, pull_operational_snapshot, test_connection

load_dotenv()


def _ensure_schema_with_retry(conn, db_path: str, attempts: int = 5, delay_seconds: float = 0.5) -> None:
    """Initialize schema once per selected DB path with lock-aware retries."""
    if st.session_state.get("_schema_ready_for") == db_path:
        return

    last_error: sqlite3.OperationalError | None = None
    for attempt in range(attempts):
        try:
            ensure_servicenow_schema(conn)
            st.session_state["_schema_ready_for"] = db_path
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(delay_seconds)

    if last_error:
        raise last_error

st.set_page_config(page_title="ServiceNow Trend Ingestor", layout="wide")
st.title("ServiceNow Trend Ingestor")
st.caption(
    "Pull incidents/requests (plus optional changes/problems) from ServiceNow into SQLite snapshots for trend analysis."
)

# --- Sidebar ----------------------------------------------------------------

with st.sidebar:
    st.header("Admin")

    st.subheader("Database")
    db_path = st.text_input("SQLite path", value=st.session_state.get("db_path", DB_PATH))
    st.session_state["db_path"] = db_path

    resolved = Path(db_path).resolve()
    if resolved.exists():
        size_kb = resolved.stat().st_size / 1024
        size_txt = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.2f} MB"
        st.caption(f"File: {resolved}  \\nSize: {size_txt}")
    else:
        st.caption(f"File: {resolved}  \\nDatabase will be created on first sync.")

    conn = get_conn(db_path)
    _ensure_schema_with_retry(conn, db_path)
    objects = get_tables(conn)
    conn.close()

    st.divider()
    st.subheader("Objects")
    for obj in objects:
        suffix = "rows" if obj["row_count"] is not None else "n/a"
        count = obj["row_count"] if obj["row_count"] is not None else "-"
        st.markdown(f"**{obj['name']}** ({obj['type']}) - {count} {suffix}")


sync_tab, trends_tab, database_tab = st.tabs(["ServiceNow Sync", "Trends", "Database"])

# --- ServiceNow Sync --------------------------------------------------------

with sync_tab:
    st.subheader("ServiceNow connection")
    instance_url = st.text_input(
        "ServiceNow Instance URL",
        value=st.session_state.get("sn_instance_url", os.getenv("SN_INSTANCE_URL", "")),
        placeholder="https://dev12345.service-now.com",
    )

    tenant_id = os.getenv("AZURE_TENANT_ID", "")
    client_id = os.getenv("AZURE_CLIENT_ID", "")
    client_secret = os.getenv("AZURE_CLIENT_SECRET", "")
    scope = os.getenv("AZURE_SCOPE") or (f"{client_id}/.default" if client_id else "")

    missing_azure = [
        key
        for key, value in {
            "AZURE_TENANT_ID": tenant_id,
            "AZURE_CLIENT_ID": client_id,
            "AZURE_CLIENT_SECRET": client_secret,
        }.items()
        if not value
    ]

    st.caption("Authentication uses Azure App Service token flow from .env only.")
    if missing_azure:
        st.warning("Missing required env vars: " + ", ".join(missing_azure))
    else:
        st.caption(f"Azure token scope: {scope}")

    with st.form("sn_auth"):
        col1, col2 = st.columns([1, 1])
        with col1:
            test_btn = st.form_submit_button("Test connection", use_container_width=True)
        with col2:
            clear_btn = st.form_submit_button("Clear session", use_container_width=True)

        if test_btn:
            if not instance_url:
                st.error("Instance URL is required.")
            elif missing_azure:
                st.error("Azure credentials are missing in .env.")
            else:
                with st.spinner("Testing ServiceNow connection..."):
                    token_result = get_azure_access_token(
                        tenant_id=tenant_id,
                        client_id=client_id,
                        client_secret=client_secret,
                        scope=scope,
                    )
                    if token_result.get("status") != "success":
                        result = token_result
                    else:
                        result = test_connection(
                            instance_url,
                            auth_method="bearer",
                            bearer_token=token_result.get("access_token"),
                        )

                st.session_state["sn_token_result"] = token_result
                st.session_state["sn_test_result"] = result
                st.session_state["sn_instance_url"] = instance_url

                if clear_btn:
                    for key in [
                        "sn_test_result",
                        "sn_token_result",
                        "sn_sync_result",
                        "sn_instance_url",
                    ]:
                        st.session_state.pop(key, None)
                    st.rerun()

            result = st.session_state.get("sn_test_result")
            token_result = st.session_state.get("sn_token_result")
            if token_result and token_result.get("status") == "success":
                st.success("Azure token acquired successfully.")
            elif token_result and token_result.get("status") != "success":
                st.error(token_result.get("message", "Failed to acquire Azure token."))

            if result:
                if result.get("status") == "success":
                    st.success(result.get("message", "Connected"))
                    st.json(result.get("user_info", {}))
                else:
                    st.error(result.get("message", "Connection failed"))

            st.divider()
            st.subheader("Snapshot sync")
            col1, col2, col3 = st.columns(3)
            with col1:
                default_since_days = int(os.getenv("SN_SINCE_DAYS", "365"))
                since_days = st.number_input(
                    "Lookback days",
                    min_value=1,
                    max_value=3650,
                    value=max(1, min(default_since_days, 3650)),
                )
            with col2:
                incident_cap = st.number_input("Incident max records", min_value=100, max_value=50000, value=5000, step=100)
            with col3:
                request_cap = st.number_input("Request item max records", min_value=100, max_value=50000, value=5000, step=100)

            include_change_requests = st.checkbox("Include change requests", value=False)
            include_problems = st.checkbox("Include problems", value=False)

            c1, c2 = st.columns(2)
            with c1:
                change_cap = st.number_input(
                    "Change request max records",
                    min_value=100,
                    max_value=50000,
                    value=3000,
                    step=100,
                    disabled=not include_change_requests,
                )
            with c2:
                problem_cap = st.number_input(
                    "Problem max records",
                    min_value=100,
                    max_value=50000,
                    value=3000,
                    step=100,
                    disabled=not include_problems,
                )

            can_sync = result and result.get("status") == "success"
            if st.button("Run snapshot sync", disabled=not can_sync):
                progress_bar = st.progress(0, text="Starting snapshot sync...")
                progress_status = st.empty()

                def update_sync_progress(progress: float, message: str) -> None:
                    progress_bar.progress(progress, text=message)
                    progress_status.caption(message)

                with st.spinner("ServiceNow snapshot is running..."):
                    token_result = get_azure_access_token(
                        tenant_id=tenant_id,
                        client_id=client_id,
                        client_secret=client_secret,
                        scope=scope,
                    )
                    if token_result.get("status") != "success":
                        sync_result = token_result
                    else:
                        sync_result = pull_operational_snapshot(
                            st.session_state.get("sn_instance_url", ""),
                            auth_method="bearer",
                            bearer_token=token_result.get("access_token"),
                            since_days=int(since_days),
                            incident_max_records=int(incident_cap),
                            request_item_max_records=int(request_cap),
                            include_change_requests=include_change_requests,
                            include_problems=include_problems,
                            change_request_max_records=int(change_cap),
                            problem_max_records=int(problem_cap),
                            progress_callback=update_sync_progress,
                        )

                if sync_result.get("status") != "success":
                    progress_bar.empty()
                    progress_status.empty()
                    st.error(sync_result.get("message", "Sync failed"))
                else:
                    conn = get_conn(db_path)
                    write_result = store_servicenow_snapshot(conn, sync_result)
                    conn.close()

                    st.session_state["sn_sync_result"] = {
                        **sync_result,
                        "write_result": write_result,
                    }
                    st.success(
                        "Sync complete. "
                        f"Stored {write_result['incident_rows']} incidents and "
                        f"{write_result['request_item_rows']} request items"
                        f"; {write_result['change_request_rows']} change requests"
                        f"; {write_result['problem_rows']} problems"
                        f" for snapshot {write_result['snapshot_date']}."
                    )
                    progress_bar.progress(1.0, text="Snapshot sync complete")

    st.divider()
    st.subheader("Scheduler setup helper")
    st.caption("Generate Task Scheduler values from your current sync options.")

    if st.button("Generate scheduler command"):
        workspace_dir = Path(__file__).resolve().parent
        default_python = workspace_dir / ".venv" / "Scripts" / "python.exe"

        program_script = str(default_python) if default_python.exists() else "python"
        start_in = str(workspace_dir)

        arg_parts = [
            "sync_snapshot.py",
            f"--since-days {int(since_days)}",
            f"--incident-max {int(incident_cap)}",
            f"--request-max {int(request_cap)}",
        ]

        if instance_url:
            arg_parts.append(f'--instance-url "{instance_url}"')

        if include_change_requests:
            arg_parts.append("--include-change-requests")
            arg_parts.append(f"--change-max {int(change_cap)}")

        if include_problems:
            arg_parts.append("--include-problems")
            arg_parts.append(f"--problem-max {int(problem_cap)}")

        add_args = " ".join(arg_parts)
        task_name = "ServiceNow Snapshot Sync"

        schtasks_cmd = (
            "schtasks /Create /F "
            f'/TN "{task_name}" '
            f'/TR "{program_script} {add_args}" '
            "/SC DAILY /ST 06:00"
        )

        st.markdown("**Task Scheduler fields**")
        st.code(
            f"Program/script:\n{program_script}\n\n"
            f"Add arguments:\n{add_args}\n\n"
            f"Start in:\n{start_in}",
            language="text",
        )

        st.markdown("**PowerShell command (optional)**")
        st.code(schtasks_cmd, language="powershell")

        st.info(
            "Security note: keep password/token in .env or Windows credential tooling. "
            "This helper does not include secrets in generated arguments."
        )


# --- Trends -----------------------------------------------------------------

with trends_tab:
    conn = get_conn(db_path)
    _ensure_schema_with_retry(conn, db_path)

    runs_df = pd.read_sql("SELECT * FROM v_snapshot_run_summary LIMIT 200", conn)
    if runs_df.empty:
        st.info("No snapshots yet. Run a ServiceNow sync first.")
        conn.close()
    else:
        st.subheader("Snapshot history")
        st.dataframe(runs_df, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Goal reporting")
        st.caption(
            "Baseline comparison is provisional until the 2026 baseline period and official SLA policy are confirmed."
        )

        snapshot_dates = sorted(
            runs_df.loc[runs_df["status"] == "success", "snapshot_date"].dropna().unique().tolist()
        )
        default_mode = "Snapshot comparison" if len(snapshot_dates) >= 2 else "Timestamp reconstruction"
        reporting_mode = st.radio(
            "Goal reporting mode",
            ["Snapshot comparison", "Timestamp reconstruction"],
            index=0 if default_mode == "Snapshot comparison" else 1,
            horizontal=True,
            help=(
                "Snapshot comparison uses two snapshot dates. "
                "Timestamp reconstruction uses opened/closed timestamps from latest records."
            ),
        )

        def goal_status(actual: float, target: float, lower_is_better: bool = False) -> str:
            return "On track" if (actual <= target if lower_is_better else actual >= target) else "Needs attention"

        baseline_incidents = current_incidents = 0
        incident_reduction = 0.0
        current_adherence = 0.0
        current_completed = 0
        baseline_aged = current_aged = 0
        backlog_reduction = 0.0

        if reporting_mode == "Snapshot comparison" and len(snapshot_dates) >= 2:
            selection_col1, selection_col2 = st.columns(2)
            with selection_col1:
                baseline_date = st.selectbox(
                    "Baseline snapshot",
                    snapshot_dates[:-1],
                    index=0,
                    help="Defaults to the earliest successful snapshot.",
                )
            with selection_col2:
                current_date = st.selectbox(
                    "Current snapshot",
                    snapshot_dates,
                    index=len(snapshot_dates) - 1,
                )

            incident_compare = pd.read_sql(
                "SELECT snapshot_date, incident_count FROM v_incident_reduction_snapshot WHERE snapshot_date IN (?, ?)",
                conn,
                params=[baseline_date, current_date],
            )
            incident_by_date = incident_compare.set_index("snapshot_date")["incident_count"].to_dict()
            baseline_incidents = int(incident_by_date.get(baseline_date, 0))
            current_incidents = int(incident_by_date.get(current_date, 0))
            incident_reduction = (
                (baseline_incidents - current_incidents) / baseline_incidents * 100
                if baseline_incidents else 0
            )

            sla_compare = pd.read_sql(
                "SELECT snapshot_date, completed_count, within_sla_count FROM v_incident_sla_adherence_snapshot WHERE snapshot_date IN (?, ?)",
                conn,
                params=[baseline_date, current_date],
            )
            sla_compare["adherence_pct"] = sla_compare["within_sla_count"].div(
                sla_compare["completed_count"].replace(0, pd.NA)
            ).mul(100).fillna(0)
            sla_by_date = sla_compare.set_index("snapshot_date").to_dict("index")
            current_sla = sla_by_date.get(current_date, {"adherence_pct": 0, "completed_count": 0})
            current_adherence = float(current_sla.get("adherence_pct", 0))
            current_completed = int(current_sla.get("completed_count", 0))

            backlog_compare = pd.read_sql(
                """
                SELECT snapshot_date, SUM(aged_open_count) AS aged_open_count
                FROM v_ticket_backlog_snapshot
                WHERE snapshot_date IN (?, ?)
                GROUP BY snapshot_date
                """,
                conn,
                params=[baseline_date, current_date],
            )
            backlog_by_date = backlog_compare.set_index("snapshot_date")["aged_open_count"].to_dict()
            baseline_aged = int(backlog_by_date.get(baseline_date, 0))
            current_aged = int(backlog_by_date.get(current_date, 0))
            backlog_reduction = (
                (baseline_aged - current_aged) / baseline_aged * 100
                if baseline_aged else 0
            )
        else:
            if reporting_mode == "Snapshot comparison" and len(snapshot_dates) < 2:
                st.info("Snapshot comparison needs at least two successful snapshot dates. Showing timestamp reconstruction.")

            window_days = st.selectbox(
                "Comparison window (days)",
                [30, 60, 90, 180],
                index=2,
                help="Compares current window against the immediately preceding window of the same length.",
            )
            today = pd.Timestamp.utcnow().normalize().tz_localize(None)
            current_start = today - pd.Timedelta(days=window_days - 1)
            baseline_end = current_start - pd.Timedelta(days=1)
            baseline_start = baseline_end - pd.Timedelta(days=window_days - 1)

            latest_incidents = pd.read_sql(
                """
                WITH ranked AS (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY sys_id ORDER BY snapshot_date DESC, pulled_at DESC
                    ) AS rn
                    FROM sn_incident_snapshot
                )
                SELECT
                    COALESCE(opened_at, sys_created_on) AS opened_at,
                    COALESCE(resolved_at, closed_at) AS closed_at,
                    priority
                FROM ranked
                WHERE rn = 1
                """,
                conn,
            )

            if latest_incidents.empty:
                st.warning("No incident records available for timestamp reconstruction.")
            else:
                opened_at = pd.to_datetime(latest_incidents["opened_at"], errors="coerce")
                closed_at = pd.to_datetime(latest_incidents["closed_at"], errors="coerce")
                priority = latest_incidents["priority"].fillna("Unknown").astype(str)

                baseline_incidents = int(((opened_at >= baseline_start) & (opened_at <= baseline_end)).sum())
                current_incidents = int(((opened_at >= current_start) & (opened_at <= today)).sum())
                incident_reduction = (
                    (baseline_incidents - current_incidents) / baseline_incidents * 100
                    if baseline_incidents else 0
                )

                closed_current = (closed_at >= current_start) & (closed_at <= today)
                resolution_hours = (closed_at - opened_at).dt.total_seconds() / 3600.0
                sla_threshold = priority.map({"1": 4, "2": 8, "3": 24, "4": 72}).fillna(120)
                within_sla = closed_current & (resolution_hours <= sla_threshold)
                current_completed = int(closed_current.sum())
                current_within = int(within_sla.sum())
                current_adherence = (current_within / current_completed * 100) if current_completed else 0

                def aged_backlog_as_of(as_of: pd.Timestamp) -> int:
                    open_on_date = (opened_at <= as_of) & (closed_at.isna() | (closed_at > as_of))
                    aged = (as_of - opened_at).dt.days >= 30
                    return int((open_on_date & aged).sum())

                baseline_aged = aged_backlog_as_of(baseline_end)
                current_aged = aged_backlog_as_of(today)
                backlog_reduction = (
                    (baseline_aged - current_aged) / baseline_aged * 100
                    if baseline_aged else 0
                )

                st.caption(
                    f"Timestamp reconstruction: baseline {baseline_start.date()} to {baseline_end.date()} "
                    f"vs current {current_start.date()} to {today.date()}."
                )

        goal_col1, goal_col2, goal_col3 = st.columns(3)
        with goal_col1:
            st.metric("Incident reduction", f"{incident_reduction:.1f}%", delta="Target 20%")
            st.caption(f"{goal_status(incident_reduction, 20)} | {baseline_incidents:,} to {current_incidents:,} incidents")
        with goal_col2:
            st.metric("SLA adherence", f"{current_adherence:.1f}%", delta="Target 90%")
            st.caption(f"{goal_status(current_adherence, 90)} | {current_completed:,} completed incidents")
        with goal_col3:
            st.metric("Aged backlog reduction", f"{backlog_reduction:.1f}%", delta="Target 30%")
            st.caption(f"{goal_status(backlog_reduction, 30)} | {baseline_aged:,} to {current_aged:,} aged tickets")

        backlog_goal = pd.read_sql(
            """
            SELECT ticket_type, SUM(open_count) AS open_count, SUM(aged_open_count) AS aged_open_count
            FROM v_ticket_backlog_monthly
            GROUP BY ticket_type
            ORDER BY ticket_type
            """
        , conn)

        if not backlog_goal.empty:
            st.markdown("**Backlog by ticket type**")
            st.dataframe(backlog_goal, use_container_width=True, hide_index=True)
            st.bar_chart(backlog_goal.set_index("ticket_type")["aged_open_count"])

        st.divider()
        st.subheader("Incident trends over time")

        daily_incident = pd.read_sql(
            """
            SELECT snapshot_date, SUM(ticket_count) AS total_incidents, SUM(open_count) AS open_incidents
            FROM v_incident_trends_daily
            GROUP BY snapshot_date
            ORDER BY snapshot_date
            """,
            conn,
        )
        if not daily_incident.empty:
            daily_incident["snapshot_date"] = pd.to_datetime(daily_incident["snapshot_date"], errors="coerce")
            daily_incident = daily_incident.set_index("snapshot_date")
            st.line_chart(daily_incident[["total_incidents", "open_incidents"]])

        incident_sla = pd.read_sql(
            """
            SELECT snapshot_date, SUM(resolved_count) AS resolved_count, SUM(breached_count) AS breached_count
            FROM v_incident_sla_daily
            GROUP BY snapshot_date
            ORDER BY snapshot_date
            """,
            conn,
        )
        if not incident_sla.empty:
            incident_sla["snapshot_date"] = pd.to_datetime(incident_sla["snapshot_date"], errors="coerce")
            incident_sla = incident_sla.set_index("snapshot_date")
            st.caption("Incident SLA trend (resolved vs breached)")
            st.line_chart(incident_sla[["resolved_count", "breached_count"]])

        top_categories = pd.read_sql(
            """
            SELECT category, SUM(ticket_count) AS tickets
            FROM v_incident_trends_daily
            GROUP BY category
            ORDER BY tickets DESC
            LIMIT 15
            """,
            conn,
        )
        if not top_categories.empty:
            st.caption("Top incident categories across all snapshots")
            st.bar_chart(top_categories.set_index("category"))

        st.divider()
        st.subheader("Request type trends over time")

        daily_requests = pd.read_sql(
            """
            SELECT snapshot_date, SUM(request_count) AS total_requests, SUM(open_count) AS open_requests
            FROM v_request_type_trends_daily
            GROUP BY snapshot_date
            ORDER BY snapshot_date
            """,
            conn,
        )
        if not daily_requests.empty:
            daily_requests["snapshot_date"] = pd.to_datetime(daily_requests["snapshot_date"], errors="coerce")
            daily_requests = daily_requests.set_index("snapshot_date")
            st.line_chart(daily_requests[["total_requests", "open_requests"]])

        top_request_types = pd.read_sql(
            """
            SELECT request_type, SUM(request_count) AS requests
            FROM v_request_type_trends_daily
            GROUP BY request_type
            ORDER BY requests DESC
            LIMIT 15
            """,
            conn,
        )
        if not top_request_types.empty:
            st.caption("Top request types (catalog items) across all snapshots")
            st.bar_chart(top_request_types.set_index("request_type"))

        change_daily = pd.read_sql(
            """
            SELECT snapshot_date, SUM(change_count) AS total_changes, SUM(open_count) AS open_changes
            FROM v_change_request_trends_daily
            GROUP BY snapshot_date
            ORDER BY snapshot_date
            """,
            conn,
        )
        if not change_daily.empty:
            st.divider()
            st.subheader("Change request trends")
            change_daily["snapshot_date"] = pd.to_datetime(change_daily["snapshot_date"], errors="coerce")
            change_daily = change_daily.set_index("snapshot_date")
            st.line_chart(change_daily[["total_changes", "open_changes"]])

        problem_daily = pd.read_sql(
            """
            SELECT snapshot_date, SUM(problem_count) AS total_problems, SUM(open_count) AS open_problems
            FROM v_problem_trends_daily
            GROUP BY snapshot_date
            ORDER BY snapshot_date
            """,
            conn,
        )
        if not problem_daily.empty:
            st.divider()
            st.subheader("Problem trends")
            problem_daily["snapshot_date"] = pd.to_datetime(problem_daily["snapshot_date"], errors="coerce")
            problem_daily = problem_daily.set_index("snapshot_date")
            st.line_chart(problem_daily[["total_problems", "open_problems"]])

            def render_problem_drilldown(container_key, snapshot_date, where_clause, where_params, dimension_label):
                status_choice = st.radio(
                    "Status filter",
                    ["All", "Open", "Closed"],
                    horizontal=True,
                    key=f"{container_key}_status",
                )
                status_sql = ""
                if status_choice == "Open":
                    status_sql = " AND (closed_at IS NULL OR closed_at = '')"
                elif status_choice == "Closed":
                    status_sql = " AND closed_at IS NOT NULL AND closed_at <> ''"
                ticket_df = pd.read_sql(
                    f"""
                    SELECT number, short_description, state, priority, opened_at, closed_at,
                           assignment_group, cmdb_ci_name
                    FROM sn_problem_snapshot
                    WHERE snapshot_date = ? AND {where_clause}{status_sql}
                    ORDER BY opened_at DESC
                    """,
                    conn,
                    params=[snapshot_date, *where_params],
                )
                st.caption(f"{len(ticket_df)} {status_choice.lower()} ticket(s) for {dimension_label}")
                st.dataframe(ticket_df, use_container_width=True, hide_index=True)
                st.download_button(
                    "Download CSV",
                    ticket_df.to_csv(index=False),
                    file_name=f"problems_{container_key}.csv",
                    mime="text/csv",
                    key=f"{container_key}_download",
                )

            st.divider()
            st.subheader("Problem status by region (open vs closed)")
            st.caption(
                "Region/territory sourced from the CI's hosting location "
                "(cmdb_ci.location -> cmn_location hierarchy), not LOB. "
                "Click a row below to list the underlying tickets."
            )
            region_status = pd.read_sql(
                """
                SELECT snapshot_date, region,
                       SUM(problem_count) AS total_problems,
                       SUM(open_count) AS open_problems,
                       SUM(closed_count) AS closed_problems
                FROM v_problem_region_territory_trends_daily
                GROUP BY snapshot_date, region
                ORDER BY snapshot_date, region
                """,
                conn,
            )
            if not region_status.empty:
                latest_status_date = region_status["snapshot_date"].max()
                latest_region_status = (
                    region_status[region_status["snapshot_date"] == latest_status_date]
                    .drop(columns="snapshot_date")
                    .sort_values("total_problems", ascending=False)
                    .reset_index(drop=True)
                )
                st.caption(f"Latest snapshot: {latest_status_date}")
                region_event = st.dataframe(
                    latest_region_status,
                    use_container_width=True,
                    hide_index=True,
                    on_select="rerun",
                    selection_mode="single-row",
                    key="region_status_table",
                )
                st.bar_chart(latest_region_status.set_index("region")[["open_problems", "closed_problems"]])

                americas_row = latest_region_status[latest_region_status["region"] == "Americas"]
                if not americas_row.empty:
                    metric_col1, metric_col2, metric_col3 = st.columns(3)
                    metric_col1.metric("Americas total", int(americas_row["total_problems"].iloc[0]))
                    metric_col2.metric("Americas open", int(americas_row["open_problems"].iloc[0]))
                    metric_col3.metric("Americas closed", int(americas_row["closed_problems"].iloc[0]))

                selected_rows = region_event.selection.rows if region_event and region_event.selection else []
                if selected_rows:
                    selected_region = latest_region_status.iloc[selected_rows[0]]["region"]
                    st.markdown(f"**Tickets for region: {selected_region}**")
                    render_problem_drilldown(
                        "region",
                        latest_status_date,
                        "COALESCE(NULLIF(cmdb_ci_region, ''), 'Unknown Region') = ?",
                        [selected_region],
                        selected_region,
                    )

                territory_status = pd.read_sql(
                    """
                    SELECT snapshot_date, territory,
                           SUM(problem_count) AS total_problems,
                           SUM(open_count) AS open_problems,
                           SUM(closed_count) AS closed_problems
                    FROM v_problem_region_territory_trends_daily
                    WHERE region = 'Americas'
                    GROUP BY snapshot_date, territory
                    ORDER BY snapshot_date, territory
                    """,
                    conn,
                )
                if not territory_status.empty:
                    latest_territory_status = (
                        territory_status[territory_status["snapshot_date"] == latest_status_date]
                        .drop(columns="snapshot_date")
                        .sort_values("total_problems", ascending=False)
                        .reset_index(drop=True)
                    )
                    st.markdown("**Americas by territory** (click a row to list tickets)")
                    territory_event = st.dataframe(
                        latest_territory_status,
                        use_container_width=True,
                        hide_index=True,
                        on_select="rerun",
                        selection_mode="single-row",
                        key="territory_status_table",
                    )
                    selected_territory_rows = (
                        territory_event.selection.rows if territory_event and territory_event.selection else []
                    )
                    if selected_territory_rows:
                        selected_territory = latest_territory_status.iloc[selected_territory_rows[0]]["territory"]
                        st.markdown(f"**Tickets for territory: {selected_territory}**")
                        render_problem_drilldown(
                            "territory",
                            latest_status_date,
                            "region = 'Americas' AND COALESCE(NULLIF(cmdb_ci_territory, ''), 'Unknown Territory') = ?",
                            [selected_territory],
                            selected_territory,
                        )

            st.divider()
            st.subheader("Problem status by customer account country (open vs closed)")
            st.caption(
                "Customer country sourced from Configuration item.u_lob -> u_customer_account.country "
                "(matches the ServiceNow Problem report's 'Configuration item.LOB...' filter). "
                "This reflects where the customer is based, not where the CI is hosted. "
                "Click a row below to list the underlying tickets."
            )
            customer_country_status = pd.read_sql(
                """
                SELECT snapshot_date, customer_country,
                       SUM(problem_count) AS total_problems,
                       SUM(open_count) AS open_problems,
                       SUM(closed_count) AS closed_problems
                FROM v_problem_customer_country_trends_daily
                GROUP BY snapshot_date, customer_country
                ORDER BY snapshot_date, customer_country
                """,
                conn,
            )
            if not customer_country_status.empty:
                latest_country_date = customer_country_status["snapshot_date"].max()
                latest_customer_country_status = (
                    customer_country_status[customer_country_status["snapshot_date"] == latest_country_date]
                    .drop(columns="snapshot_date")
                    .sort_values("total_problems", ascending=False)
                    .reset_index(drop=True)
                )
                st.caption(f"Latest snapshot: {latest_country_date}")
                country_event = st.dataframe(
                    latest_customer_country_status,
                    use_container_width=True,
                    hide_index=True,
                    on_select="rerun",
                    selection_mode="single-row",
                    key="customer_country_status_table",
                )

                americas_countries = latest_customer_country_status[
                    latest_customer_country_status["customer_country"].isin(["US", "CA"])
                ]
                if not americas_countries.empty:
                    metric_col1, metric_col2, metric_col3 = st.columns(3)
                    metric_col1.metric("US+CA total", int(americas_countries["total_problems"].sum()))
                    metric_col2.metric("US+CA open", int(americas_countries["open_problems"].sum()))
                    metric_col3.metric("US+CA closed", int(americas_countries["closed_problems"].sum()))

                selected_country_rows = country_event.selection.rows if country_event and country_event.selection else []
                if selected_country_rows:
                    selected_country = latest_customer_country_status.iloc[selected_country_rows[0]]["customer_country"]
                    st.markdown(f"**Tickets for customer country: {selected_country}**")
                    render_problem_drilldown(
                        "customer_country",
                        latest_country_date,
                        "COALESCE(NULLIF(cmdb_ci_customer_relationship_region, ''), 'Unknown') = ?",
                        [selected_country],
                        selected_country,
                    )

            region_goal = pd.read_sql(
                """
                SELECT snapshot_date, region, open_count, aged_open_count, aged_open_pct
                FROM v_problem_goal_region_snapshot
                ORDER BY snapshot_date, region
                """,
                conn,
            )
            territory_goal = pd.read_sql(
                """
                SELECT snapshot_date, territory, open_count, aged_open_count, aged_open_pct
                FROM v_problem_goal_territory_snapshot
                ORDER BY snapshot_date, territory
                """,
                conn,
            )
            if not region_goal.empty or not territory_goal.empty:
                lens = st.radio(
                    "Problem goal lens",
                    ["Region", "Territory"],
                    horizontal=True,
                    key="problem_goal_lens",
                )
                goal_df = region_goal if lens == "Region" else territory_goal
                dim_col = "region" if lens == "Region" else "territory"
                st.caption(f"{lens} aged-backlog goal lens (source: CI hosting location hierarchy)")
                success_dates = sorted(
                    runs_df.loc[runs_df["status"] == "success", "snapshot_date"].dropna().unique().tolist()
                )
                if len(success_dates) >= 2:
                    baseline_date = success_dates[0]
                    current_date = success_dates[-1]
                    compare = goal_df[goal_df["snapshot_date"].isin([baseline_date, current_date])].copy()
                    if not compare.empty:
                        pivot = compare.pivot_table(
                            index=dim_col,
                            columns="snapshot_date",
                            values="aged_open_count",
                            aggfunc="sum",
                            fill_value=0,
                        )
                        if baseline_date in pivot.columns and current_date in pivot.columns:
                            regional_cmp = pd.DataFrame(
                                {
                                    dim_col: pivot.index,
                                    "baseline_aged_open": pivot[baseline_date].astype(int),
                                    "current_aged_open": pivot[current_date].astype(int),
                                }
                            )
                            regional_cmp["aged_backlog_reduction_pct"] = regional_cmp.apply(
                                lambda r: (
                                    (r["baseline_aged_open"] - r["current_aged_open"])
                                    / r["baseline_aged_open"]
                                    * 100
                                )
                                if r["baseline_aged_open"]
                                else 0,
                                axis=1,
                            )
                            regional_cmp = regional_cmp.sort_values("current_aged_open", ascending=False)
                            st.dataframe(regional_cmp, use_container_width=True, hide_index=True)
                else:
                    latest_region = (
                        goal_df[goal_df["snapshot_date"] == goal_df["snapshot_date"].max()]
                        .sort_values("aged_open_count", ascending=False)
                    )
                    st.dataframe(latest_region, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("How to analyze trend data")
        st.markdown(
            """
            1. Use `snapshot_date` as the x-axis in Power BI or SQL queries.
            2. Use `v_incident_trends_daily` for incident volume/open-close patterns by category and state.
            3. Use `v_request_type_trends_daily` for request type adoption and backlog trends.
            4. Use `v_incident_sla_daily` for resolved/breached SLA trend lines by day and priority.
            5. Use `v_change_request_trends_daily` and `v_problem_trends_daily` when those domains are enabled.
            6. Use `v_incident_latest` and `v_request_item_latest` when you only want the latest known state.
            """
        )

        conn.close()


# --- Database ---------------------------------------------------------------

with database_tab:
    conn = get_conn(db_path)
    objs = get_tables(conn)
    if not objs:
        st.info("Database is empty.")
    else:
        for obj in objs:
            with st.expander(f"{obj['name']} ({obj['type']})"):
                st.caption(f"Columns: {', '.join(obj['columns'])}")

                try:
                    preview = pd.read_sql(f"SELECT * FROM [{obj['name']}] LIMIT 100", conn)
                    st.dataframe(preview, use_container_width=True)
                except Exception as exc:
                    st.warning(f"Could not preview object: {exc}")

                if obj["type"] == "view" or obj["name"].startswith("_"):
                    st.caption("Protected object. Deletion disabled.")
                    continue

                pending_key = f"pending_delete_{obj['name']}"
                if st.button(f"Delete table {obj['name']}", key=f"del_{obj['name']}"):
                    st.session_state[pending_key] = True

                if st.session_state.get(pending_key):
                    st.warning(f"This will permanently remove {obj['name']}.")
                    c1, c2 = st.columns([1, 3])
                    with c1:
                        if st.button("Confirm", key=f"confirm_del_{obj['name']}"):
                            drop_table(conn, obj["name"])
                            st.session_state.pop(pending_key, None)
                            st.rerun()
                    with c2:
                        if st.button("Cancel", key=f"cancel_del_{obj['name']}"):
                            st.session_state.pop(pending_key, None)
                            st.rerun()

    conn.close()
