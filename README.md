# Excel Data Ingestor

A Streamlit app for loading Excel spreadsheets into a SQLite database. Upload files directly or pull them from Outlook, preview the data, and query from Power BI.

---

## How it works

```
Upload .xlsx files  ─or─  Pull from Outlook
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
| Email integration | MSAL + Microsoft Graph API |
| Database | SQLite |
| Analytics | Power BI Desktop (ODBC) |

## Prerequisites

- Python 3.10 or later — [download here](https://www.python.org/downloads/)
- pip (included with Python)
- An **Azure AD App Registration** with `Mail.Read` delegated permission (only needed for the Outlook tab)

## Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/your-username/excel-ingester-db-loader.git
   cd excel-ingester-db-loader
   ```

2. Create a virtual environment (recommended):
   ```bash
   python -m venv .venv
   .venv\Scripts\activate        # Windows
   # source .venv/bin/activate   # macOS / Linux
   ```

3. Install dependencies:
   ```bash
   pip install streamlit pandas openpyxl msal requests
   ```

## Running the app

```bash
streamlit run app.py
```

This opens the app in your default browser at `http://localhost:8501`. The SQLite database is created automatically at `db/data.sqlite` on first load.

## Usage

1. Go to the **Upload** tab
2. Drag and drop one or more `.xlsx` files
3. Review the preview — edit the target table name if needed
4. Click **Load** to write to SQLite
5. Switch to the **Database** tab to browse loaded tables and view data

### Outlook tab

1. Enter your Azure AD **Client ID** and **Tenant ID**
2. Click **Sign in to Outlook** — you'll get a device code to enter at microsoft.com/devicelogin
3. Pick a mail folder (and subfolder) to search
4. Click **Fetch messages with attachments** to see recent emails
5. Expand a message, preview the Excel attachment, and load it directly to the database

The **Admin** sidebar (visible on every tab) lets you:

- Change the SQLite database path — useful for working with multiple databases
- See the database file location and size
- View a summary of all loaded tables with row and column counts

## Running tests

```bash
pip install pytest
python -m pytest tests/ -v
```

## Connecting Power BI

1. Install the [SQLite ODBC driver](http://www.ch-werner.de/sqliteodbc/)
2. In Windows **ODBC Data Source Administrator**, create a User DSN pointing to `db/data.sqlite`
3. In Power BI Desktop: **Get Data → ODBC** → select your DSN
4. Select tables, build relationships, create visuals

## Future Enhancements

- Scheduled Outlook pulls — auto-fetch new attachments on a timer
- Duplicate detection — warn if the same file has been loaded before
- Multi-sheet support — select specific sheets from multi-tab workbooks
- Column mapping — rename columns before loading
- PostgreSQL migration — swap SQLite for Postgres when ready

---

*Excel Data Ingestor · v1.1.0*
