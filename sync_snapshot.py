"""CLI utility for running scheduled ServiceNow snapshot syncs."""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from db import DB_PATH, ensure_servicenow_schema, get_conn, store_servicenow_snapshot
from servicenow import pull_operational_snapshot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ServiceNow snapshot sync into SQLite")
    parser.add_argument("--db-path", default=os.getenv("DB_PATH", DB_PATH), help="SQLite database path")
    parser.add_argument("--instance-url", default=os.getenv("SN_INSTANCE_URL"), help="ServiceNow instance URL")
    parser.add_argument(
        "--auth-mode",
        default=os.getenv("SN_AUTH_MODE", "basic"),
        choices=["basic", "bearer"],
        help="Auth mode: basic or bearer",
    )
    parser.add_argument("--username", default=os.getenv("SN_USERNAME"), help="ServiceNow username")
    parser.add_argument("--password", default=os.getenv("SN_PASSWORD"), help="ServiceNow password")
    parser.add_argument("--bearer-token", default=os.getenv("SN_BEARER_TOKEN"), help="ServiceNow bearer token")
    parser.add_argument("--since-days", type=int, default=int(os.getenv("SN_SINCE_DAYS", "30")))
    parser.add_argument("--incident-max", type=int, default=int(os.getenv("SN_INCIDENT_MAX", "5000")))
    parser.add_argument("--request-max", type=int, default=int(os.getenv("SN_REQUEST_MAX", "5000")))
    parser.add_argument("--include-change-requests", action="store_true")
    parser.add_argument("--include-problems", action="store_true")
    parser.add_argument("--change-max", type=int, default=int(os.getenv("SN_CHANGE_MAX", "3000")))
    parser.add_argument("--problem-max", type=int, default=int(os.getenv("SN_PROBLEM_MAX", "3000")))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("SN_TIMEOUT", "20")))
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    if not args.instance_url:
        print("ERROR: Missing instance URL. Set SN_INSTANCE_URL or pass --instance-url.", file=sys.stderr)
        return 2

    if args.auth_mode == "basic" and (not args.username or not args.password):
        print("ERROR: Basic auth requires --username and --password (or SN_USERNAME/SN_PASSWORD).", file=sys.stderr)
        return 2

    if args.auth_mode == "bearer" and not args.bearer_token:
        print("ERROR: Bearer auth requires --bearer-token (or SN_BEARER_TOKEN).", file=sys.stderr)
        return 2

    snapshot = pull_operational_snapshot(
        args.instance_url,
        auth_method=args.auth_mode,
        username=args.username,
        password=args.password,
        bearer_token=args.bearer_token,
        since_days=args.since_days,
        incident_max_records=args.incident_max,
        request_item_max_records=args.request_max,
        include_change_requests=args.include_change_requests,
        include_problems=args.include_problems,
        change_request_max_records=args.change_max,
        problem_max_records=args.problem_max,
        timeout=args.timeout,
    )

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
