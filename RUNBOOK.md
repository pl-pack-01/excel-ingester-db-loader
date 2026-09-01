# Operations Runbook

Use this as the quick reference for daily work.

## Start The Dashboard

From the project root:

```powershell
& .\.venv\Scripts\python.exe -m streamlit run app.py
```

Default URL:

```text
http://localhost:8502
```

If the port is busy:

```powershell
& .\.venv\Scripts\python.exe -m streamlit run app.py --server.port 8503
```

## Clear Streamlit Ports / Kill Stale App Processes

If tabs disappear, pages look stale, or SQLite locks persist, clear running
Streamlit processes and free the ports before restarting.

Stop all Streamlit app.py Python processes:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*streamlit run app.py*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Force-clear listeners on ports 8502 and 8503:

```powershell
$ports = 8502,8503
foreach ($p in $ports) {
  Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
}
```

Verify ports are free (no output means clear):

```powershell
Get-NetTCPConnection -LocalPort 8502,8503 -State Listen -ErrorAction SilentlyContinue |
  Select-Object LocalPort,OwningProcess,State
```

## Run A Full ServiceNow Sync (Burst Mode)

Recommended large pull command:

```powershell
& .\.venv\Scripts\python.exe sync_snapshot.py `
  --since-days 365 `
  --burst-days 30 `
  --request-max 0 `
  --incident-max 5000 `
  --change-max 3000 `
  --problem-max 3000 `
  --include-change-requests `
  --include-problems
```

Notes:
- `--burst-days` splits the pull into windows and refreshes auth per window.
- `--request-max 0` means unlimited paging.
- If a burst fails, rerun the same command after fixing auth/API issues.

## Quick Validation

Run tests:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests -q
```

## Confirm Latest Snapshot Counts

```powershell
& .\.venv\Scripts\python.exe -c 'import sqlite3, os; p="db/data.sqlite"; print("db_size", os.path.getsize(p) if os.path.exists(p) else "missing"); c=sqlite3.connect(p); print([tuple(r) for r in c.execute("select snapshot_date,incident_count,request_item_count,change_request_count,problem_count,status from sn_sync_runs order by id desc limit 5")]); c.close()'
```

## Goal Reporting Modes

In the Trends tab:
- Use `Snapshot comparison` when you have at least two successful snapshots.
- Use `Timestamp reconstruction` for faster reporting from open/close timestamps.

Regional goal lens:
- Problem regional goals are normalized into `United States`, `EMEA`, `APAC`, `Latin America`, or source region label.
- SQL views:
  - `v_problem_region_normalized_daily`
  - `v_problem_goal_region_snapshot`

## Common Issues

Authentication failure during long runs:
- Use burst mode (`--burst-days 30` or smaller).
- Avoid running multiple syncs at the same time.

Unexpected empty/old numbers:
- Confirm app is pointed at `db/data.sqlite` in the sidebar.
- Verify latest rows in `sn_sync_runs` with the command above.