"""CLI utility for running scheduled ServiceNow snapshot syncs."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from dotenv import load_dotenv

from db import DB_PATH, ensure_servicenow_schema, get_conn, store_servicenow_snapshot
from servicenow import get_azure_access_token, pull_operational_snapshot


def _optional_int_env(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    raw = raw.strip()
    if raw == "":
        return None
    return int(raw)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ServiceNow snapshot sync into SQLite")
    parser.add_argument("--db-path", default=os.getenv("DB_PATH", DB_PATH), help="SQLite database path")
    parser.add_argument("--instance-url", default=os.getenv("SN_INSTANCE_URL"), help="ServiceNow instance URL")
    parser.add_argument("--since-days", type=int, default=int(os.getenv("SN_SINCE_DAYS", "365")))
    parser.add_argument(
        "--burst-days",
        type=int,
        default=0,
        help="Split the lookback into date windows and acquire a fresh token per window; 0 disables burst mode.",
    )
    parser.add_argument("--incident-max", type=int, default=int(os.getenv("SN_INCIDENT_MAX", "5000")))
    parser.add_argument(
        "--request-max",
        type=int,
        default=_optional_int_env("SN_REQUEST_MAX"),
        help=(
            "Max request item records. Use 0 for unlimited. "
            "If omitted, >=365-day runs are unlimited and shorter runs default to 5000."
        ),
    )
    parser.add_argument("--include-change-requests", action="store_true")
    parser.add_argument("--include-problems", action="store_true")
    parser.add_argument("--change-max", type=int, default=int(os.getenv("SN_CHANGE_MAX", "3000")))
    parser.add_argument("--problem-max", type=int, default=int(os.getenv("SN_PROBLEM_MAX", "3000")))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("SN_TIMEOUT", "20")))
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    explicit_request_max = "--request-max" in sys.argv
    args = parse_args()

    if not args.instance_url:
        print("ERROR: Missing instance URL. Set SN_INSTANCE_URL or pass --instance-url.", file=sys.stderr)
        return 2

    tenant_id = os.getenv("AZURE_TENANT_ID", "")
    client_id = os.getenv("AZURE_CLIENT_ID", "")
    client_secret = os.getenv("AZURE_CLIENT_SECRET", "")
    scope = os.getenv("AZURE_SCOPE") or (f"{client_id}/.default" if client_id else None)

    if not explicit_request_max and args.since_days >= 365:
        request_max = None
    elif args.request_max is None:
        request_max = 5000
    else:
        request_max = None if args.request_max <= 0 else args.request_max

    if args.burst_days < 0:
        print("ERROR: --burst-days must be zero or greater.", file=sys.stderr)
        return 2

    def get_token() -> str | None:
        token_result = get_azure_access_token(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            scope=scope,
            timeout=args.timeout,
        )
        if token_result.get("status") != "success":
            print(f"ERROR: {token_result.get('message', 'Failed to acquire Azure token')}", file=sys.stderr)
            return None
        return token_result.get("access_token")

    def pull(token: str, operational_query: str | None = None) -> dict[str, Any]:
        return pull_operational_snapshot(
            args.instance_url,
            auth_method="bearer",
            bearer_token=token,
            since_days=args.since_days,
            incident_max_records=args.incident_max,
            request_item_max_records=request_max,
            include_change_requests=args.include_change_requests,
            include_problems=args.include_problems,
            change_request_max_records=args.change_max,
            problem_max_records=args.problem_max,
            timeout=args.timeout,
            operational_query=operational_query,
        )

    if args.burst_days == 0:
        token = get_token()
        if not token:
            return 2
        snapshot = pull(token)
    else:
        end_date = datetime.now(timezone.utc).date() + timedelta(days=1)
        start_date = end_date - timedelta(days=max(args.since_days, 1))
        combined: dict[str, dict[str, dict[str, Any]]] = {
            "incidents": {},
            "request_items": {},
            "change_requests": {},
            "problems": {},
        }
        window_start = start_date
        while window_start < end_date:
            window_end = min(window_start + timedelta(days=args.burst_days), end_date)
            query = (
                f"sys_updated_on>={window_start.isoformat()} 00:00:00"
                f"^sys_updated_on<{window_end.isoformat()} 00:00:00"
            )
            if window_end == end_date:
                query = f"active=true^NQ{query}"
            print(f"Starting burst {window_start} through {window_end}...", flush=True)
            token = get_token()
            if not token:
                return 2
            burst = pull(token, query)
            if burst.get("status") != "success":
                print(f"ERROR: Burst {window_start} failed: {burst.get('message', 'Unknown sync error')}", file=sys.stderr)
                return 1
            for dataset in combined:
                for row in burst.get(dataset, []):
                    sys_id = str(row.get("sys_id", ""))
                    if sys_id:
                        combined[dataset][sys_id] = row
            print(
                f"Completed burst {window_start} through {window_end}: "
                f"{sum(len(rows) for rows in combined.values()):,} unique records so far.",
                flush=True,
            )
            window_start = window_end

        token_timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        snapshot = {
            "status": "success",
            "pulled_at": token_timestamp,
            "snapshot_date": datetime.now(timezone.utc).date().isoformat(),
            "since_days": args.since_days,
            **{dataset: list(rows.values()) for dataset, rows in combined.items()},
            "message": f"Burst sync completed with {args.burst_days}-day windows",
        }

    if snapshot.get("status") != "success":
        print(f"ERROR: {snapshot.get('message', 'Unknown sync error')}", file=sys.stderr)
        return 1

    conn = get_conn(args.db_path)
    ensure_servicenow_schema(conn)
    write_result = store_servicenow_snapshot(conn, snapshot)
    conn.close()

    print(
        "Sync succeeded: "
        f"snapshot_date={write_result['snapshot_date']} "
        f"incidents={write_result['incident_rows']} "
        f"requests={write_result['request_item_rows']} "
        f"changes={write_result['change_request_rows']} "
        f"problems={write_result['problem_rows']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
