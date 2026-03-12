# Excel Data Ingestor

A simple Streamlit app for loading Excel spreadsheets into a SQLite database. Upload files, preview the data, confirm, and query from Power BI.

---

## How it works

```
Upload .xlsx files in the browser
        ↓
Preview columns, data types, and sample rows
        ↓
Pick a table name (auto-suggested from filename)
        ↓
Load into SQLite
        ↓
Power BI connects for reporting
```

## Tech Stack

| Layer | Technology |
|---|---|
| UI | Streamlit |
| Data processing | pandas + openpyxl |
| Database | SQLite |
| Analytics | Power BI Desktop (ODBC) |

## Setup

```bash
pip install streamlit pandas openpyxl
streamlit run app.py
```

## Usage

1. Open the app in your browser (Streamlit launches automatically)
2. Upload one or more `.xlsx` files
3. Review the preview — edit the target table name if needed
4. Click **Load** to write to SQLite
5. Browse loaded tables and view data in the **Database** tab

The SQLite file is saved at `db/data.sqlite` by default.

## Connecting Power BI

1. Install the [SQLite ODBC driver](http://www.ch-werner.de/sqliteodbc/)
2. In Windows **ODBC Data Source Administrator**, create a User DSN pointing to `db/data.sqlite`
3. In Power BI Desktop: **Get Data → ODBC** → select your DSN
4. Select tables, build relationships, create visuals

## Future Enhancements

- Duplicate detection — warn if the same file has been loaded before
- Multi-sheet support — select specific sheets from multi-tab workbooks
- Column mapping — rename columns before loading
- PostgreSQL migration — swap SQLite for Postgres when ready
- AI-assisted table routing and column mapping

---

*Excel Data Ingestor · v1.0.0*
