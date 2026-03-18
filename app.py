"""Streamlit app — upload Excel files, preview, and load into SQLite."""

import io
from pathlib import Path

import streamlit as st
import pandas as pd

from db import get_conn, get_tables, DB_PATH
from ingest import read_excel, table_name_from_filename, normalise_columns, load_to_db
from outlook import (
    build_msal_app,
    get_token_interactive,
    complete_device_flow,
    list_mail_folders,
    list_child_folders,
    list_messages_with_attachments,
    list_attachments,
    download_attachment,
)

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
    tables = get_tables(conn)
    if tables:
        for t in tables:
            st.markdown(f"**{t['name']}** — {t['row_count']} rows, {len(t['columns'])} cols")
    else:
        st.caption("No tables yet.")
    conn.close()


# ── Tabs ────────────────────────────────────────────────────────────────────

upload_tab, outlook_tab, database_tab = st.tabs(["Upload", "Outlook", "Database"])

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

GRAPH_SCOPES = ["Mail.Read"]

with outlook_tab:
    st.subheader("Download Excel attachments from Outlook")

    col_id, col_tenant = st.columns(2)
    with col_id:
        client_id = st.text_input("App Registration Client ID", type="password")
    with col_tenant:
        tenant_id = st.text_input("Azure AD Tenant ID", type="password")

    if client_id and tenant_id:
        # Build MSAL app (cached in session state)
        if "msal_app" not in st.session_state or st.session_state.get("_msal_key") != (client_id, tenant_id):
            st.session_state["msal_app"] = build_msal_app(client_id, tenant_id)
            st.session_state["_msal_key"] = (client_id, tenant_id)
            st.session_state.pop("outlook_token", None)

        app = st.session_state["msal_app"]

        # Auth flow
        if "outlook_token" not in st.session_state:
            if st.button("Sign in to Outlook"):
                flow = get_token_interactive(app, GRAPH_SCOPES)
                if isinstance(flow, str):
                    # Got token from cache
                    st.session_state["outlook_token"] = flow
                    st.rerun()
                else:
                    st.session_state["device_flow"] = flow
                    st.rerun()

            if "device_flow" in st.session_state:
                flow = st.session_state["device_flow"]
                st.info(f"Go to **https://microsoft.com/devicelogin** and enter code: **{flow['user_code']}**")
                if st.button("I've completed sign-in"):
                    try:
                        token = complete_device_flow(app, flow)
                        st.session_state["outlook_token"] = token
                        st.session_state.pop("device_flow", None)
                        st.rerun()
                    except RuntimeError as e:
                        st.error(str(e))
        else:
            token = st.session_state["outlook_token"]

            # Folder picker
            try:
                if "mail_folders" not in st.session_state:
                    st.session_state["mail_folders"] = list_mail_folders(token)

                folders = st.session_state["mail_folders"]
                folder_map = {f["displayName"]: f["id"] for f in folders}
                selected_folder_name = st.selectbox("Mail folder", list(folder_map.keys()))

                if selected_folder_name:
                    parent_id = folder_map[selected_folder_name]

                    # Check for child folders
                    cache_key = f"child_folders_{parent_id}"
                    if cache_key not in st.session_state:
                        st.session_state[cache_key] = list_child_folders(token, parent_id)

                    children = st.session_state[cache_key]
                    target_folder_id = parent_id

                    if children:
                        child_map = {c["displayName"]: c["id"] for c in children}
                        child_name = st.selectbox("Subfolder", ["(none — use parent)"] + list(child_map.keys()))
                        if child_name != "(none — use parent)":
                            target_folder_id = child_map[child_name]

                    # Fetch messages
                    if st.button("Fetch messages with attachments"):
                        st.session_state["outlook_messages"] = list_messages_with_attachments(token, target_folder_id)

                    if "outlook_messages" in st.session_state:
                        messages = st.session_state["outlook_messages"]
                        if not messages:
                            st.info("No messages with attachments found in this folder.")
                        else:
                            for msg in messages:
                                sender = msg.get("from", {}).get("emailAddress", {}).get("address", "unknown")
                                received = msg.get("receivedDateTime", "")[:10]
                                with st.expander(f"{received} — {msg['subject']} (from {sender})"):
                                    atts = list_attachments(token, msg["id"])
                                    xlsx_atts = [a for a in atts if a["name"].lower().endswith((".xlsx", ".xls"))]

                                    if not xlsx_atts:
                                        st.caption("No Excel attachments.")
                                    else:
                                        for att in xlsx_atts:
                                            size_kb = att.get("size", 0) / 1024
                                            st.markdown(f"**{att['name']}** ({size_kb:.1f} KB)")

                                            btn_key = f"dl_{msg['id']}_{att['id']}"
                                            if st.button(f"Download & preview", key=btn_key):
                                                name, content = download_attachment(token, msg["id"], att["id"])
                                                df = read_excel(io.BytesIO(content))
                                                df = normalise_columns(df)

                                                st.session_state[f"outlook_df_{btn_key}"] = df
                                                st.session_state[f"outlook_name_{btn_key}"] = name

                                            if f"outlook_df_{btn_key}" in st.session_state:
                                                df = st.session_state[f"outlook_df_{btn_key}"]
                                                name = st.session_state[f"outlook_name_{btn_key}"]
                                                suggested = table_name_from_filename(name)
                                                table_name = st.text_input("Table name", value=suggested, key=f"tn_{btn_key}")
                                                st.dataframe(df.head(10), use_container_width=True)
                                                st.caption(f"{len(df)} rows, {len(df.columns)} columns")

                                                if st.button(f"Load to database", key=f"load_{btn_key}"):
                                                    conn = get_conn(db_path)
                                                    rows = load_to_db(conn, df, table_name)
                                                    conn.close()
                                                    st.success(f"Loaded {rows} rows into `{table_name}`")

            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code == 401:
                    st.warning("Session expired. Please sign in again.")
                    st.session_state.pop("outlook_token", None)
                    st.session_state.pop("mail_folders", None)
                    st.rerun()
                else:
                    st.error(f"Graph API error: {e}")

            if st.button("Sign out"):
                for key in list(st.session_state.keys()):
                    if key.startswith(("outlook_", "mail_folders", "child_folders", "msal_", "_msal", "device_flow")):
                        del st.session_state[key]
                st.rerun()

# ── Database tab ────────────────────────────────────────────────────────────

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

    conn.close()
