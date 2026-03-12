"""Streamlit app — upload Excel files, preview, and load into SQLite."""

import os
from pathlib import Path

import streamlit as st
import pandas as pd

from db import get_conn, get_tables, DB_PATH
from ingest import read_excel, table_name_from_filename, normalise_columns, load_to_db

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

upload_tab, database_tab = st.tabs(["Upload", "Database"])

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
