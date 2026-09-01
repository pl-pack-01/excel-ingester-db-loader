# ServiceNow Trend Ingestor

A Streamlit app that pulls operational data directly from ServiceNow and stores daily snapshots in SQLite so you can analyze trends for incidents and request types over time.

## What changed

Quick command reference: see [RUNBOOK.md](RUNBOOK.md).

This project no longer depends on Excel spreadsheets as its primary source.

Data now flows like this:

```text
ServiceNow REST API
    -> Snapshot pull (incident + sc_req_item + optional change/problem)
    -> SQLite snapshot tables
    -> SQL trend views
    -> Streamlit charts / Power BI
```

## Why snapshots matter for trends

ServiceNow tables represent current state. To analyze trends over time (volume, backlog, open vs closed), you need historical snapshots.

Each sync run stores:
- `snapshot_date` (business date for the pull)
- `pulled_at` (exact timestamp)
- incident rows
- request item rows
- optional change request rows
- optional problem rows

Over time, this creates a time series without modifying ServiceNow itself.

## Tech stack

- UI: Streamlit
- API access: requests
- Data layer: SQLite
- Analytics: SQL views + Power BI

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -e .
```

3. Configure environment variables (optional, can also be entered in app UI):

```env
SN_INSTANCE_URL=https://your-instance.service-now.com
AZURE_TENANT_ID=your-tenant-id
AZURE_CLIENT_ID=your-client-id
AZURE_CLIENT_SECRET=your-client-secret
# Optional. If empty, defaults to <AZURE_CLIENT_ID>/.default
AZURE_SCOPE=api://[app-id-uri]/.default
```

4. Run the app:

```bash
streamlit run app.py
```

## Using the app

### 1) Test connectivity

In the **ServiceNow Sync** tab:
- provide instance URL
- ensure Azure env vars are set in `.env`
- click **Test connection**

### 2) Pull a snapshot

- set lookback window (`since_days`)
- set record caps for incidents and request items
- optionally enable change requests and problems
- click **Run snapshot sync**

The app stores data into:
- `sn_incident_snapshot`
- `sn_request_item_snapshot`
- `sn_change_request_snapshot` (optional)
- `sn_problem_snapshot` (optional)
- `sn_sync_runs`

### 3) Analyze trends

Use built-in charts in **Trends** tab or query these views:
- `v_incident_trends_daily`
- `v_request_type_trends_daily`
- `v_incident_sla_daily`
- `v_change_request_trends_daily`
- `v_problem_trends_daily`
- `v_problem_region_territory_trends_daily`
- `v_incident_latest`
- `v_request_item_latest`
- `v_snapshot_run_summary`

## Automated daily sync

Use the CLI to run unattended snapshot jobs:

```bash
python sync_snapshot.py --since-days 365 --include-change-requests --include-problems
```

For large lookbacks, use burst mode. This splits the pull into date windows,
acquires a fresh token for each window, deduplicates records, and writes one
combined snapshot only after every burst succeeds:

```bash
python sync_snapshot.py --since-days 365 --burst-days 30 --request-max 0 --include-change-requests --include-problems
```

The final burst also includes all currently active incidents and request items,
so aged backlog reporting is not limited to recently updated records. If a
burst fails, no partial snapshot is written; rerun the command after resolving
the API or authentication issue.

For 12-month baseline runs, request items auto-page without a 5000 cap unless you explicitly set `--request-max` (or `SN_REQUEST_MAX`).
Set `--request-max 0` to force unlimited pulls.

Windows Task Scheduler action example:

```text
Program/script:
    C:\Users\mipack\OneDrive - SAS\Documents\Workspace\Projects\excel-ingester-db-loader\.venv\Scripts\python.exe

Add arguments:
    sync_snapshot.py --since-days 365 --include-change-requests --include-problems

Start in:
    C:\Users\mipack\OneDrive - SAS\Documents\Workspace\Projects\excel-ingester-db-loader
```

This picks up values from `.env`, acquires an Entra access token, and writes a new snapshot run every day.

## Recommended trend metrics

For incidents:
- daily incident count by category
- daily open incident count (backlog)
- state mix trend (e.g. New/In Progress/Resolved)
- SLA trend: resolved volume vs breached volume by day/priority

For request types:
- daily request volume by `cat_item` (request type)
- open request item trend over time
- request type share trend by week/month

For change/problem domains (optional):
- daily change request volume and open trend
- daily problem volume and open trend
- daily problem trend by CI region and territory

## Example SQL queries

Incident volume trend:

```sql
SELECT snapshot_date, SUM(ticket_count) AS total_incidents
FROM v_incident_trends_daily
GROUP BY snapshot_date
ORDER BY snapshot_date;
```

Open backlog trend by incident category:

```sql
SELECT snapshot_date, category, SUM(open_count) AS open_incidents
FROM v_incident_trends_daily
GROUP BY snapshot_date, category
ORDER BY snapshot_date, open_incidents DESC;
```

Request type trend:

```sql
SELECT snapshot_date, request_type, SUM(request_count) AS total_requests
FROM v_request_type_trends_daily
GROUP BY snapshot_date, request_type
ORDER BY snapshot_date;
```

Incident SLA trend:

```sql
SELECT snapshot_date, priority, SUM(resolved_count) AS resolved, SUM(breached_count) AS breached
FROM v_incident_sla_daily
GROUP BY snapshot_date, priority
ORDER BY snapshot_date, priority;
```

## Tests

Run tests with:

```bash
python -m pytest tests/ -v
```

## Notes

- Run at least one sync per day to get stable daily trend lines.
- If you need near-real-time trends, run snapshots more frequently and use `pulled_at` granularity.
- Record caps are safeguards; increase them if your table volumes are larger.
