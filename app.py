"""Streamlit app for ingesting ServiceNow data into SQLite and analyzing trends."""

from __future__ import annotations

import os
from pathlib import Path

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
from servicenow import pull_operational_snapshot, test_connection

load_dotenv()

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
    ensure_servicenow_schema(conn)
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

    auth_mode = st.radio(
        "Authentication mode",
        options=["basic", "bearer"],
        format_func=lambda mode: "Username + Password" if mode == "basic" else "Bearer Token",
        horizontal=True,
    )

    instance_url = st.text_input(
        "ServiceNow Instance URL",
        value=st.session_state.get("sn_instance_url", os.getenv("SN_INSTANCE_URL", "")),
        placeholder="https://dev12345.service-now.com",
    )

    with st.form("sn_auth"):
        username = ""
        password = ""
        bearer_token = ""

        if auth_mode == "basic":
            username = st.text_input(
                "Username",
                value=st.session_state.get("sn_username", os.getenv("SN_USERNAME", "")),
            )
            password = st.text_input(
                "Password",
                value=st.session_state.get("sn_password", os.getenv("SN_PASSWORD", "")),
                type="password",
            )
        else:
            bearer_token = st.text_input(
                "Bearer token",
                value=st.session_state.get("sn_bearer_token", ""),
                type="password",
            )

        col1, col2 = st.columns([1, 1])
        with col1:
            test_btn = st.form_submit_button("Test connection", use_container_width=True)
        with col2:
            clear_btn = st.form_submit_button("Clear session", use_container_width=True)

        if test_btn:
            if not instance_url:
                st.error("Instance URL is required.")
            elif auth_mode == "basic" and (not username or not password):
                st.error("Username and password are required for basic auth.")
            elif auth_mode == "bearer" and not bearer_token:
                st.error("Bearer token is required.")
            else:
                with st.spinner("Testing ServiceNow connection..."):
                    result = test_connection(
                        instance_url,
                        auth_method=auth_mode,
                        username=username,
                        password=password,
                        bearer_token=bearer_token,
                    )
                st.session_state["sn_test_result"] = result
                st.session_state["sn_instance_url"] = instance_url
                st.session_state["sn_auth_mode"] = auth_mode
                if auth_mode == "basic":
                    st.session_state["sn_username"] = username
                    st.session_state["sn_password"] = password
                    st.session_state.pop("sn_bearer_token", None)
                else:
                    st.session_state["sn_bearer_token"] = bearer_token
                    st.session_state.pop("sn_username", None)
                    st.session_state.pop("sn_password", None)

        if clear_btn:
            for key in [
                "sn_test_result",
                "sn_sync_result",
                "sn_instance_url",
                "sn_auth_mode",
                "sn_username",
                "sn_password",
                "sn_bearer_token",
            ]:
                st.session_state.pop(key, None)
            st.rerun()

    result = st.session_state.get("sn_test_result")
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
        since_days = st.number_input("Lookback days", min_value=1, max_value=365, value=30)
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
        with st.spinner("Pulling incidents and request items from ServiceNow..."):
            sync_result = pull_operational_snapshot(
                st.session_state.get("sn_instance_url", ""),
                auth_method=st.session_state.get("sn_auth_mode", "basic"),
                username=st.session_state.get("sn_username"),
                password=st.session_state.get("sn_password"),
                bearer_token=st.session_state.get("sn_bearer_token"),
                since_days=int(since_days),
                incident_max_records=int(incident_cap),
                request_item_max_records=int(request_cap),
                include_change_requests=include_change_requests,
                include_problems=include_problems,
                change_request_max_records=int(change_cap),
                problem_max_records=int(problem_cap),
            )

        if sync_result.get("status") != "success":
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

    sync_result = st.session_state.get("sn_sync_result")
    if sync_result:
        with st.expander("Latest sync details", expanded=False):
            st.json(
                {
                    "snapshot_date": sync_result.get("snapshot_date"),
                    "pulled_at": sync_result.get("pulled_at"),
                    "since_days": sync_result.get("since_days"),
                    "incident_count": sync_result.get("incident_count"),
                    "request_item_count": sync_result.get("request_item_count"),
                    "change_request_count": sync_result.get("change_request_count"),
                    "problem_count": sync_result.get("problem_count"),
                    "write_result": sync_result.get("write_result"),
                }
            )

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
            f"--auth-mode {auth_mode}",
        ]

        if instance_url:
            arg_parts.append(f'--instance-url "{instance_url}"')

        if auth_mode == "basic" and username:
            arg_parts.append(f'--username "{username}"')

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
    ensure_servicenow_schema(conn)

    runs_df = pd.read_sql("SELECT * FROM v_snapshot_run_summary LIMIT 200", conn)
    if runs_df.empty:
        st.info("No snapshots yet. Run a ServiceNow sync first.")
        conn.close()
    else:
        st.subheader("Snapshot history")
        st.dataframe(runs_df, use_container_width=True, hide_index=True)

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
