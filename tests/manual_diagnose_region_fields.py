"""Manual diagnostic: dump full CI/location records to find the real region/territory field.

Run manually against a live ServiceNow instance (uses .env credentials). Not part of pytest.
This does NOT guess field names - it fetches full records (sysparm_fields omitted) and prints
every field whose name or value looks region/territory/americas related, plus writes full JSON
to a file for manual inspection.

Usage:
    python tests/manual_diagnose_region_fields.py
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from servicenow import _build_auth_and_headers, _request_json, get_azure_access_token, _normalise_instance_url

load_dotenv(Path(__file__).parent.parent / ".env")

INSTANCE_URL = _normalise_instance_url(os.environ["SN_INSTANCE_URL"])
KEYWORDS = ("region", "territory", "americas", "geo", "country", "continent", "area")


def get_bearer_headers():
    token_result = get_azure_access_token(
        tenant_id=os.environ["AZURE_TENANT_ID"],
        client_id=os.environ["AZURE_CLIENT_ID"],
        client_secret=os.environ["AZURE_CLIENT_SECRET"],
        scope=os.environ.get("AZURE_SCOPE"),
    )
    if token_result.get("status") != "success":
        raise SystemExit(f"Token error: {token_result.get('message')}")
    return {
        "Authorization": f"Bearer {token_result['access_token']}",
        "Accept": "application/json",
    }


def fetch_full_records(headers, table, query, limit=5):
    result = _request_json(
        "GET",
        f"{INSTANCE_URL}/api/now/table/{table}",
        auth=None,
        headers=headers,
        params={
            "sysparm_query": query,
            "sysparm_display_value": "all",
            "sysparm_limit": str(limit),
            "sysparm_exclude_reference_link": "true",
        },
    )
    if result.get("status") != "success":
        print(f"ERROR fetching {table}: {result.get('message')}")
        return []
    return result.get("payload", {}).get("result", [])


def print_matches(label, record):
    print(f"\n--- {label} (sys_id={record.get('sys_id')}) matching fields ---")
    found = False
    for key, value in sorted(record.items()):
        display = value.get("display_value") if isinstance(value, dict) else value
        haystack = f"{key} {display}".lower()
        if any(kw in haystack for kw in KEYWORDS):
            print(f"  {key}: {display!r}")
            found = True
    if not found:
        print("  (no matches)")


def main():
    headers = get_bearer_headers()

    print("Fetching a few problems with a CI set...")
    problems = fetch_full_records(headers, "problem", "cmdb_ciISNOTEMPTY", limit=5)
    if not problems:
        print("No problem records with cmdb_ci found.")
        return

    dump_dir = Path(__file__).parent / "_diagnostic_output"
    dump_dir.mkdir(exist_ok=True)

    ci_ids = []
    for problem in problems:
        ci_ref = problem.get("cmdb_ci") or {}
        ci_id = ci_ref.get("value") if isinstance(ci_ref, dict) else ci_ref
        if ci_id:
            ci_ids.append(ci_id)
        print_matches(f"problem {problem.get('number')}", problem)

    (dump_dir / "problems.json").write_text(json.dumps(problems, indent=2))

    ci_ids = sorted(set(ci_ids))
    if not ci_ids:
        print("No CI sys_ids resolved from problems.")
        return

    print(f"\nFetching full cmdb_ci records for {len(ci_ids)} CI(s)...")
    ci_query = f"sys_idIN{','.join(ci_ids)}"
    cis = fetch_full_records(headers, "cmdb_ci", ci_query, limit=len(ci_ids))
    for ci in cis:
        print_matches(f"cmdb_ci {ci.get('name')}", ci)
    (dump_dir / "cmdb_ci.json").write_text(json.dumps(cis, indent=2))

    location_ids = sorted(
        {
            (ci.get("location") or {}).get("value")
            for ci in cis
            if isinstance(ci.get("location"), dict) and ci["location"].get("value")
        }
    )
    if location_ids:
        print(f"\nFetching full cmn_location records for {len(location_ids)} location(s)...")
        loc_query = f"sys_idIN{','.join(location_ids)}"
        locations = fetch_full_records(headers, "cmn_location", loc_query, limit=len(location_ids))
        for location in locations:
            print_matches(f"cmn_location {location.get('name')}", location)
        (dump_dir / "cmn_location.json").write_text(json.dumps(locations, indent=2))

    print(f"\nFull JSON dumps written to {dump_dir}")


if __name__ == "__main__":
    main()
