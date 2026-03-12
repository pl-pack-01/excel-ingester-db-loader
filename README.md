# Excel Data Ingestor

A browser-based tool for loading Excel spreadsheets into a SQLite (or PostgreSQL) database, with AI-assisted table routing and column mapping. Built for analysts who want clean, queryable data without manual ETL work.

---

## Table of Contents

1. [Overview](#overview)
2. [Tech Stack](#tech-stack)
3. [Architecture](#architecture)
4. [Build Phases](#build-phases)
5. [AI Analysis — Multi-Provider Setup](#ai-analysis--multi-provider-setup)
6. [Connecting Power BI](#connecting-power-bi)
7. [Milestones](#milestones)
8. [Future Enhancements](#future-enhancements)

---

## Overview

Upload one or more `.xlsx` / `.xls` files through a browser UI. The app:

- Routes each file to the correct database table based on its filename
- Shows a preview before anything is written
- Warns you if the same file has been loaded before and asks how to handle it
- Optionally runs AI analysis to suggest the correct table and map incoming columns to your schema
- Writes clean, typed data to SQLite — ready for Power BI or any analytics tool

Designed to migrate to PostgreSQL with a single config change when you're ready.

---

## Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| UI | HTML + CSS + JS | Single-page, served by Flask — no build step |
| Server | Python / Flask | Handles upload, preview, and ingest API routes |
| Data processing | pandas + openpyxl | Reads `.xlsx`/`.xls`, infers types, normalises columns |
| Database | SQLite → PostgreSQL | Zero-install to start; swap connection string for Postgres |
| AI analysis | Claude / Gemini / ChatGPT | User-selectable provider; pluggable adapter pattern |
| Analytics | Power BI Desktop | Native ODBC (SQLite) or direct connector (PostgreSQL) |
| Config | `table_config.json` | Maps filename regex patterns to target table names |

---

## Architecture

### Data flow

```
User drops .xlsx files
        ↓
Flask receives + parses with pandas
        ↓
Filename matched against table_config.json → target table
        ↓
Preview returned to UI (columns, sample rows, row count, duplicate flag)
        ↓
[Optional] User clicks "Analyze with AI"
        ↓
AI provider suggests table + column mappings with confidence scores
        ↓
User reviews, edits if needed, confirms
        ↓
Data written to SQLite · ingest logged to _ingest_log
        ↓
Power BI connects for reporting
```

### Filename-based routing

A `table_config.json` file holds pattern → table mappings:

```json
{
  "mappings": [
    { "pattern": "sales",      "table": "sales" },
    { "pattern": "inv(oice)?", "table": "invoices" },
    { "pattern": "inventory",  "table": "inventory" }
  ]
}
```

Patterns are regex strings matched case-insensitively against the base filename. Files with no match get a table name auto-derived from the filename. Mappings are editable through the UI config screen.

### Duplicate handling

Every ingested file is MD5-fingerprinted. If the same file is re-uploaded, the UI shows a warning with three options:

- **Skip** — do nothing, move on
- **Append** — add rows even if they already exist
- **Replace** — truncate the table and reload from this file

All ingests are recorded in a `_ingest_log` table for auditability.

### SQLite → PostgreSQL migration path

The DB connection is isolated to a single `db.py` module. Switching to PostgreSQL requires:

1. `pip install psycopg2-binary`
2. Change `DB_PATH` in `.env` to a `postgresql://` connection string

No other code changes needed.

---

## Build Phases

### Phase 1 — Foundation (~2–3 hrs)

Goal: project scaffold, Flask running, core read-and-write loop proven with a test file.

| Task | Deliverable | Est. |
|---|---|---|
| Project scaffold | `/uploads`, `/db`, `/static`, `app.py`, `table_config.json` | 20 min |
| Install dependencies | `pip install flask pandas openpyxl` | 5 min |
| Flask skeleton | Single route returning "Hello" — confirms server runs | 15 min |
| DB module | `db.py`: `get_conn()`, table creation, `_ingest_log` schema | 30 min |
| Ingest function | Read `.xlsx` with pandas, write to SQLite via `to_sql()` | 45 min |
| Filename router | Load `table_config.json`, regex match filename → table | 30 min |
| Duplicate check | MD5 hash file, query `_ingest_log` for prior match | 20 min |
| Manual test | Ingest 2–3 sample files via Python REPL, verify in SQLite | 30 min |

---

### Phase 2 — Upload API (~2 hrs)

Goal: all ingest logic exposed as HTTP endpoints. Fully functional via curl or Postman by end of phase.

| Endpoint | Description | Est. |
|---|---|---|
| `POST /api/preview` | Accept file(s), return columns, sample rows, row count, table, duplicate flag | 45 min |
| `POST /api/ingest` | Accept decisions JSON (`append`/`replace`/`skip`), execute write, return results | 30 min |
| `GET /api/tables` | List all tables with column names and row counts | 20 min |
| `GET /api/table/<n>/sample` | Return first 50 rows of any table | 15 min |
| `GET /api/log` | Return last 100 ingest log entries | 10 min |
| `POST /api/config` | Save updated filename mappings from UI | 15 min |
| `GET /api/schema` | Return full DB schema as JSON (used by AI prompt builder) | 15 min |
| Error handling | Wrap all routes in try/except, return structured JSON errors | 20 min |

---

### Phase 3 — User Interface (~3–4 hrs)

Goal: complete single-page UI served by Flask. Everything in one HTML file — no build process.

| Screen | Description | Est. |
|---|---|---|
| Upload screen | Drag-and-drop zone, multi-file, visual drag-over feedback | 45 min |
| Preview cards | Per-file: filename, detected table (editable), row/col count, 5-row sample | 60 min |
| Duplicate warning | Amber banner with Skip / Append / Replace buttons | 30 min |
| Confirm & ingest | Single "Ingest All" button, progress feedback, success/error toasts | 30 min |
| Tables browser | List all DB tables with row counts; click to view 50-row sample | 45 min |
| Ingest log | Scrollable history: filename, table, rows written, timestamp | 20 min |
| Config editor | Add / edit / remove filename pattern → table mappings | 45 min |

**UI principles:**
- Dark, utilitarian aesthetic — no distracting decorations
- Monospace font for table data; sans-serif for UI chrome
- All state in JavaScript memory — no localStorage or cookies
- Keyboard-navigable, ARIA labels on interactive elements

---

### Phase 4 — AI Analysis (~3–4 hrs)

Goal: on-demand AI button that suggests table routing and column mappings using your choice of AI provider. See [AI Analysis — Multi-Provider Setup](#ai-analysis--multi-provider-setup) for full details.

| Task | Deliverable | Est. |
|---|---|---|
| Provider adapter | `ai/base.py` interface + `claude.py`, `gemini.py`, `openai.py` implementations | 60 min |
| `POST /api/ai-analyze` | Build prompt from file context + live DB schema, call selected provider, return JSON | 60 min |
| Prompt engineering | Iterate until structured JSON output is reliable across 5+ file types | 45 min |
| Confidence parser | Map scores to high/low/none tiers; handle malformed responses gracefully | 20 min |
| UI: Analyze button | Add "Analyze with AI" + provider selector dropdown to each preview card | 25 min |
| UI: Suggestion overlay | Table suggestion + per-column mapping table with confidence badges | 60 min |
| UI: Flag & review | Amber rows require explicit accept/edit/reject before ingest is allowed | 30 min |
| UI: Apply mappings | Rename DataFrame columns to confirmed targets before writing to DB | 30 min |
| Error handling | Timeout, rate limit, bad JSON — fall back gracefully to manual | 20 min |

---

### Phase 5 — Polish & Power BI (~1–2 hrs)

Goal: harden for real-world use, document the Power BI connection.

| Task | Deliverable | Est. |
|---|---|---|
| Column normalisation | Strip whitespace, lowercase, replace spaces with underscores | 20 min |
| Type inference | pandas dtypes + cast obvious date columns; log failures | 30 min |
| Bulk load stress test | 10+ files simultaneously; verify all land correctly | 30 min |
| Power BI guide | ODBC SQLite steps + PostgreSQL upgrade path | 30 min |
| README | Setup instructions, dependency list, how to run, how to add patterns | 20 min |
| `.env` config | DB path, upload folder, API keys as environment variables | 15 min |

---

## AI Analysis — Multi-Provider Setup

The AI analysis layer uses a **pluggable adapter pattern** — one common interface, swappable providers. The user selects their preferred provider from a dropdown in the UI; the selection is sent with each `/api/ai-analyze` request.

### Provider interface (`ai/base.py`)

```python
class AIProvider:
    def analyze(self, filename: str, columns: list, sample_rows: list, schema: dict) -> dict:
        """
        Returns:
        {
          "suggested_table": "sales",
          "table_confidence": 0.92,
          "table_reasoning": "...",
          "column_mappings": [
            { "source": "Inv Date", "target": "invoice_date", "confidence": 0.95 },
            { "source": "Amt $",    "target": "amount",       "confidence": 0.78 }
          ]
        }
        """
        raise NotImplementedError
```

### Implementations

**`ai/claude.py`** — Anthropic Claude

```python
import anthropic

class ClaudeProvider(AIProvider):
    def __init__(self):
        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    def analyze(self, filename, columns, sample_rows, schema):
        response = self.client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_prompt(filename, columns, sample_rows, schema)}]
        )
        return parse_response(response.content[0].text)
```

**`ai/gemini.py`** — Google Gemini

```python
import google.generativeai as genai

class GeminiProvider(AIProvider):
    def __init__(self):
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        self.model = genai.GenerativeModel("gemini-1.5-pro")

    def analyze(self, filename, columns, sample_rows, schema):
        response = self.model.generate_content(build_prompt(filename, columns, sample_rows, schema))
        return parse_response(response.text)
```

**`ai/openai.py`** — OpenAI ChatGPT

```python
from openai import OpenAI

class OpenAIProvider(AIProvider):
    def __init__(self):
        self.client = OpenAI()  # reads OPENAI_API_KEY from env

    def analyze(self, filename, columns, sample_rows, schema):
        response = self.client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": build_prompt(filename, columns, sample_rows, schema)}
            ]
        )
        return parse_response(response.choices[0].message.content)
```

### Prompt structure

All three providers receive the same prompt content:

```
SYSTEM:
You are a data analyst. Given an incoming Excel file and a target database
schema, suggest the best table and column mappings. Respond ONLY in valid
JSON — no preamble, no markdown fences.

USER:
Filename: sales_march_2026.xlsx

Incoming columns + sample data:
[["Inv Date","Customer","Amt $","Region"], ["2026-03-01","Acme",1200,"NE"], ...]

Target database schema:
{ "sales": ["id","invoice_date","customer_name","amount","region"], ... }
```

### Confidence thresholds

| Score | Status | UI treatment |
|---|---|---|
| ≥ 0.85 | High confidence | Green badge · pre-filled · editable |
| 0.60 – 0.84 | Low confidence | Amber badge + flag · pre-filled · must be explicitly accepted |
| < 0.60 | No suggestion | Field left blank · grey "Could not determine" label |

### Environment variables

Add these to a `.env` file (never commit to source control):

```
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=AI...
OPENAI_API_KEY=sk-...
DB_PATH=db/data.sqlite
UPLOAD_FOLDER=uploads
```

Install all three SDKs upfront so any provider works without extra setup:

```bash
pip install anthropic google-generativeai openai python-dotenv
```

---

## Connecting Power BI

### SQLite (initial)

Power BI connects via ODBC:

1. Install the free [SQLite ODBC driver](http://www.ch-werner.de/sqliteodbc/)
2. In Windows **ODBC Data Source Administrator**, create a User DSN pointing to `db/data.sqlite`
3. In Power BI Desktop: **Get Data → ODBC** → select your DSN
4. Select tables, build relationships in Model view, create measures and visuals

### PostgreSQL (when ready)

1. `pip install psycopg2-binary`
2. Update `DB_PATH` in `.env` to `postgresql://user:pass@host:5432/dbname`
3. In Power BI Desktop: **Get Data → PostgreSQL Database**
4. For automated refresh, set up Power BI Gateway

---

## Milestones

| # | Milestone | Success criterion | Phase |
|---|---|---|---|
| M1 | Core ingest works | Upload a `.xlsx` via Python REPL and see data in SQLite | 1 |
| M2 | API complete | `curl POST /api/preview` returns correct JSON for a test file | 2 |
| M3 | UI functional | Browser upload → preview → confirm → data in DB (full loop) | 3 |
| M4 | AI analysis live | "Analyze with AI" returns table + column suggestions with confidence scores | 4 |
| M5 | Bulk load verified | 10 files of different types load correctly with AI suggestions applied | 5 |
| M6 | Power BI connected | At least one table visible and queryable in Power BI Desktop | 5 |

---

## Future Enhancements

- **Automated ingest** — watched folder or scheduled task; eliminates manual upload
- **PostgreSQL migration** — already designed for; one connection string change
- **AI mapping memory** — persist confirmed column mappings so the model learns your schema over time
- **Data validation rules** — flag rows that fail type or business rule checks before loading
- **Multi-sheet support** — select specific sheets from multi-tab workbooks
- **Incremental loads** — load only new rows based on a timestamp or ID column
- **User authentication** — lightweight login for shared/team use

---

*Excel Data Ingestor · v1.1*