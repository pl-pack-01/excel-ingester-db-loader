"""Streamlit app — upload Excel files, preview, and load into SQLite."""

import streamlit as st
import pandas as pd

from db import get_conn, get_tables
from ingest import read_excel, table_name_from_filename, normalise_columns, load_to_db

st.set_page_config(page_title="Excel Data Ingestor", layout="wide")
st.title("Excel Data Ingestor")

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
                conn = get_conn()
                rows = load_to_db(conn, df, table_name)
                conn.close()
                st.success(f"Loaded {rows} rows into `{table_name}`")

# ── Database tab ────────────────────────────────────────────────────────────

with database_tab:
    conn = get_conn()
    tables = get_tables(conn)

    if not tables:
        st.info("No tables yet. Upload some files first.")
    else:
        for table in tables:
            with st.expander(f"**{table['name']}** — {table['row_count']} rows"):
                df = pd.read_sql(f"SELECT * FROM [{table['name']}] LIMIT 100", conn)
                st.dataframe(df, use_container_width=True)

    conn.close()
