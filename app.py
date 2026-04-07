"""Streamlit app — upload Excel files, preview, and load into SQLite."""

from pathlib import Path

import streamlit as st
import pandas as pd

from db import get_conn, get_tables, drop_table, DB_PATH, ensure_ingest_log, is_already_ingested, record_ingest, ensure_incidents_view
from ingest import read_excel, table_name_from_filename, normalise_columns, load_to_db
from outlook import scan_for_attachments
from servicenow import test_connection, query_table

st.set_page_config(page_title="Excel Data Ingestor", layout="wide")
st.title("Excel Data Ingestor")

# ── Sidebar: Admin ──────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Admin")

    st.subheader("Database connection")
    db_path = st.text_input("SQLite path", value=st.session_state.get("db_path", DB_PATH))
    st.session_state["db_path"] = db_path

    resolved = Path(db_path).resolve()
    if resolved.exists():
        size_kb = resolved.stat().st_size / 1024
        if size_kb < 1024:
            st.caption(f"File: {resolved}  \nSize: {size_kb:.1f} KB")
        else:
            st.caption(f"File: {resolved}  \nSize: {size_kb / 1024:.2f} MB")
    else:
        st.caption(f"File: {resolved}  \nDatabase will be created on first load.")

    st.divider()
    st.subheader("Loaded tables")
    conn = get_conn(db_path)
    ensure_incidents_view(conn)
    tables = get_tables(conn)
    if tables:
        for t in tables:
            st.markdown(f"**{t['name']}** — {t['row_count']} rows, {len(t['columns'])} cols")
    else:
        st.caption("No tables yet.")
    conn.close()


# ── Tabs ────────────────────────────────────────────────────────────────────

upload_tab, outlook_tab, servicenow_tab, database_tab, charts_tab = st.tabs(["Upload", "Outlook", "ServiceNow", "Database", "Charts"])

# ── Upload tab ──────────────────────────────────────────────────────────────

with upload_tab:
    files = st.file_uploader(
        "Upload Excel files",
        type=["xlsx", "xls"],
        accept_multiple_files=True,
    )

    if files:
        for file in files:
            st.divider()
            df = read_excel(file)
            df = normalise_columns(df)

            suggested = table_name_from_filename(file.name)
            col1, col2 = st.columns([1, 3])

            with col1:
                table_name = st.text_input(
                    "Table name",
                    value=suggested,
                    key=f"table_{file.name}",
                )
                st.caption(f"{len(df)} rows, {len(df.columns)} columns")

            with col2:
                st.dataframe(df.head(10), use_container_width=True)

            if st.button(f"Load **{file.name}**", key=f"load_{file.name}"):
                conn = get_conn(db_path)
                rows = load_to_db(conn, df, table_name)
                conn.close()
                st.success(f"Loaded {rows} rows into `{table_name}`")
                st.rerun()

# ── Outlook tab ─────────────────────────────────────────────────────────────

with outlook_tab:
    st.subheader("Import from Outlook")

    col1, col2, col3 = st.columns(3)
    with col1:
        days_back = st.number_input("Days back", min_value=1, value=30)
    with col2:
        subfolder = st.text_input("Inbox subfolder (optional)", value="")
    with col3:
        subject_filter = st.text_input("Subject contains (optional)", value="")

    if st.button("Scan for Excel attachments"):
        with st.spinner("Scanning Outlook…"):
            try:
                attachments, diag = scan_for_attachments(
                    subfolder=subfolder,
                    days_back=int(days_back),
                    subject_filter=subject_filter,
                )
                st.session_state["outlook_attachments"] = attachments
                st.session_state["outlook_diag"] = diag
            except Exception as e:
                st.error(str(e))
                st.session_state.pop("outlook_attachments", None)
                st.session_state.pop("outlook_diag", None)

    attachments = st.session_state.get("outlook_attachments")
    diag = st.session_state.get("outlook_diag")

    if diag is not None:
        st.caption(
            f"Folder: **{diag.folder_name}** · "
            f"Date range: {diag.date_from} → {diag.date_to}"
        )

        with st.expander("Scan details", expanded=(len(attachments) == 0)):
            st.table(
                pd.DataFrame(
                    [
                        ("Total items in folder", diag.total_items),
                        ("Skipped — not a mail item", diag.skipped_non_mail),
                        ("Skipped — older than date range", diag.skipped_too_old),
                        ("In date range", diag.in_date_range),
                        ("Skipped — no attachments", diag.skipped_no_attachment),
                        ("Skipped — subject filter", diag.skipped_subject_filter),
                        ("Skipped — non-Excel attachment", diag.skipped_non_excel),
                        ("Excel attachments found", diag.excel_attachments_found),
                    ],
                    columns=["Stage", "Count"],
                )
            )
            if diag.errors:
                st.warning(f"{len(diag.errors)} error(s) during scan:")
                for err in diag.errors:
                    st.code(err, language=None)

        if attachments:
            conn_check = get_conn(db_path)
            ensure_ingest_log(conn_check)
            already_ingested = {
                i
                for i, a in enumerate(attachments)
                if is_already_ingested(conn_check, a.filename, a.received)
            }
            conn_check.close()

            select_all = st.checkbox(
                "Select all for import",
                value=st.session_state.get("outlook_select_all", False),
                key="outlook_select_all",
            )

            att_df = pd.DataFrame(
                [
                    {
                        "Import": select_all and i not in already_ingested,
                        "Already imported": i in already_ingested,
                        "Subject": a.subject,
                        "From": a.sender,
                        "Received": a.received,
                        "Filename": a.filename,
                    }
                    for i, a in enumerate(attachments)
                ]
            )

            edited = st.data_editor(
                att_df,
                column_config={
                    "Import": st.column_config.CheckboxColumn("Import", default=False),
                    "Already imported": st.column_config.CheckboxColumn("Already imported"),
                },
                disabled=["Already imported", "Subject", "From", "Received", "Filename"],
                use_container_width=True,
                hide_index=True,
                key=f"outlook_table_{select_all}_{st.session_state.get('outlook_import_version', 0)}",
            )

            selected_indices = edited.index[edited["Import"]].tolist()

            if selected_indices and st.button(f"Import {len(selected_indices)} selected"):
                conn = get_conn(db_path)
                ensure_ingest_log(conn)
                for idx in selected_indices:
                    att = attachments[idx]
                    if is_already_ingested(conn, att.filename, att.received):
                        st.warning(f"Skipped **{att.filename}** — already imported (received {att.received})")
                        continue
                    try:
                        df = read_excel(att.temp_path)
                        df = normalise_columns(df)
                        df["_source_file"] = att.filename
                        df["_ingested_at"] = att.received
                        table = table_name_from_filename(att.filename)
                        rows = load_to_db(conn, df, table)
                        record_ingest(conn, att.filename, att.received, table, rows)
                        st.success(f"Loaded {rows} rows from **{att.filename}** into `{table}`")
                    except Exception as e:
                        st.error(f"Failed to import **{att.filename}**: {e}")
                conn.close()
                st.session_state["outlook_import_version"] = (
                    st.session_state.get("outlook_import_version", 0) + 1
                )
                st.rerun()

    elif "outlook_diag" in st.session_state:
        st.info("No Excel attachments found matching those criteria.")


# ── ServiceNow tab ──────────────────────────────────────────────────────────

with servicenow_tab:
    st.subheader("ServiceNow Authentication & Testing")
    st.caption("Use basic login by default, or switch to API token auth when available.")

    auth_mode = st.radio(
        "Authentication mode",
        options=["basic", "bearer"],
        format_func=lambda mode: "Username + Password" if mode == "basic" else "API Bearer Token",
        horizontal=True,
    )

    instance_url = st.text_input(
        "ServiceNow Instance URL",
        value=st.session_state.get("sn_instance_url", ""),
        placeholder="e.g., https://dev12345.service-now.com or dev12345.service-now.com",
        help="Your ServiceNow instance URL",
    )

    st.markdown("#### Authentication")
    with st.form("sn_auth"):
        username = ""
        password = ""
        bearer_token = ""

        if auth_mode == "basic":
            username = st.text_input(
                "Username",
                value=st.session_state.get("sn_username", ""),
            )
            password = st.text_input(
                "Password",
                value=st.session_state.get("sn_password", ""),
                type="password",
                help="Your ServiceNow password (kept in session only)",
            )
        else:
            bearer_token = st.text_input(
                "Bearer token",
                value=st.session_state.get("sn_bearer_token", ""),
                type="password",
                help="API bearer token (kept in session only)",
            )

        col1, col2 = st.columns(2)
        with col1:
            test_btn = st.form_submit_button("Test connection", use_container_width=True)
        with col2:
            clear_btn = st.form_submit_button("Clear", use_container_width=True)

        if test_btn:
            if not instance_url:
                st.error("Please provide instance URL.")
            elif auth_mode == "basic" and (not username or not password):
                st.error("Please fill in username and password.")
            elif auth_mode == "bearer" and not bearer_token:
                st.error("Please provide bearer token.")
            else:
                with st.spinner("Testing connection..."):
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
                "sn_username",
                "sn_password",
                "sn_bearer_token",
                "sn_test_result",
                "sn_query_result",
                "sn_instance_url",
                "sn_auth_mode",
            ]:
                st.session_state.pop(key, None)
            st.rerun()
    
    # Display test results
    result = st.session_state.get("sn_test_result")
    if result:
        st.divider()
        if result["status"] == "success":
            st.success(result["message"])
            
            with st.expander("User information", expanded=True):
                user_info = result.get("user_info", {})
                st.json(user_info)
            
            with st.expander("Query sample tables"):
                st.markdown("#### Sample Tables")
                col1, col2 = st.columns([2, 1])
                
                with col1:
                    table_name = st.selectbox(
                        "Select a table to query",
                        options=["incident", "change_request", "problem", "request", "cmn_location", "sys_user"],
                        index=0,
                    )
                
                with col2:
                    limit = st.number_input("Limit records", min_value=1, max_value=100, value=10)
                
                if st.button("Query table"):
                    if st.session_state.get("sn_instance_url"):
                        with st.spinner(f"Querying {table_name}…"):
                            active_mode = st.session_state.get("sn_auth_mode", "basic")
                            result = query_table(
                                st.session_state["sn_instance_url"],
                                table_name,
                                auth_method=active_mode,
                                username=st.session_state.get("sn_username"),
                                password=st.session_state.get("sn_password"),
                                bearer_token=st.session_state.get("sn_bearer_token"),
                                limit=limit,
                            )
                            st.session_state["sn_query_result"] = result
        else:
            st.error(result.get("message", "Connection failed"))
    
    # Display query results
    query_result = st.session_state.get("sn_query_result")
    if query_result and query_result.get("status") == "success":
        st.divider()
        records = query_result.get("records", [])
        
        if records:
            st.markdown(f"#### {query_result.get('table', 'Results').upper()} ({query_result.get('count')} records)")
            
            # Convert to DataFrame for nice display
            df = pd.DataFrame(records)
            # Show first 100 columns and truncate long values
            display_cols = list(df.columns)[:100]
            st.dataframe(df[display_cols], use_container_width=True, height=400)
            
            # Option to view raw JSON
            with st.expander("Raw JSON"):
                st.json(records)
        else:
            st.info(f"No records found in {query_result.get('table')}")
    elif query_result and query_result.get("status") == "error":
        st.error(query_result.get("message", "Query failed"))


# ── Database tab ─────────────────────────────────────────────────────────────

with database_tab:
    conn = get_conn(db_path)
    tables = get_tables(conn)

    if not tables:
        st.info("No tables yet. Upload some files first.")
    else:
        for table in tables:
            with st.expander(f"**{table['name']}** — {table['row_count']} rows"):
                st.caption(f"Columns: {', '.join(table['columns'])}")
                df = pd.read_sql(f"SELECT * FROM [{table['name']}] LIMIT 100", conn)
                st.dataframe(df, use_container_width=True)

                if table["name"].startswith("_"):
                    st.caption("System table — cannot be deleted.")
                    continue

                pending_key = f"pending_delete_{table['name']}"
                if st.button(f"Delete table `{table['name']}`", key=f"del_{table['name']}", type="secondary"):
                    st.session_state[pending_key] = True

                if st.session_state.get(pending_key):
                    st.warning(f"This will permanently drop **{table['name']}** and all its data.")
                    confirm_col, cancel_col = st.columns([1, 5])
                    with confirm_col:
                        if st.button("Confirm delete", key=f"confirm_del_{table['name']}", type="primary"):
                            drop_conn = get_conn(db_path)
                            drop_table(drop_conn, table['name'])
                            drop_conn.close()
                            st.session_state.pop(pending_key, None)
                            st.rerun()
                    with cancel_col:
                        if st.button("Cancel", key=f"cancel_del_{table['name']}"):
                            st.session_state.pop(pending_key, None)
                            st.rerun()

    conn.close()

# ── Charts tab ───────────────────────────────────────────────────────────────

with charts_tab:
    conn = get_conn(db_path)
    all_tables = get_tables(conn)
    data_tables = [t for t in all_tables if not t["name"].startswith("_")]

    if not data_tables:
        st.info("No data tables yet. Import some files first.")
        conn.close()
    else:
        # ── Volume overview ──────────────────────────────────────────────────
        st.subheader("Volume by table")
        overview_df = (
            pd.DataFrame({"Rows": {t["name"]: t["row_count"] for t in data_tables}})
            .sort_values("Rows")
        )
        st.bar_chart(overview_df, horizontal=True)

        st.divider()

        # ── Per-table deep-dive ──────────────────────────────────────────────
        st.subheader("Deep dive")
        selected_table = st.selectbox(
            "Select a table",
            [t["name"] for t in data_tables],
            key="charts_table",
        )

        df = pd.read_sql(f"SELECT * FROM [{selected_table}]", conn)
        conn.close()
        existing = set(df.columns)
        lob_col = "lob" if "lob" in existing else "reporter_lob" if "reporter_lob" in existing else None

        left, right = st.columns(2)
        slot = [left, right]
        slot_idx = 0

        def _next_slot():
            global slot_idx
            c = slot[slot_idx % 2]
            slot_idx += 1
            return c

        if "state" in existing:
            with _next_slot():
                st.markdown("**By state**")
                st.bar_chart(df["state"].value_counts())

        if "assignment_group" in existing and df["assignment_group"].notna().any():
            with _next_slot():
                st.markdown("**Top 15 assignment groups**")
                st.bar_chart(df["assignment_group"].value_counts().head(15))

        if "territory" in existing and df["territory"].notna().any():
            with _next_slot():
                st.markdown("**By territory**")
                st.bar_chart(df["territory"].value_counts())

        if "region" in existing and df["region"].notna().any():
            with _next_slot():
                st.markdown("**By region**")
                st.bar_chart(df["region"].value_counts())

        if lob_col and df[lob_col].notna().any():
            with _next_slot():
                st.markdown("**By LOB**")
                st.bar_chart(df[lob_col].value_counts())

        if "created" in existing:
            created = pd.to_datetime(df["created"], errors="coerce").dropna()
            if not created.empty:
                trend = (
                    created.dt.to_period("M")
                    .value_counts()
                    .sort_index()
                )
                trend.index = trend.index.to_timestamp()
                trend.name = "Created"
                st.markdown("**Created over time (monthly)**")
                st.line_chart(trend)
