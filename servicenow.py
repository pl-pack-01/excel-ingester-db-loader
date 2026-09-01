"""ServiceNow REST API client with auth helpers and snapshot ingestion utilities."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Optional

import requests
import time
from requests.auth import HTTPBasicAuth


DEFAULT_TIMEOUT = 20
CI_LOOKUP_BATCH_SIZE = 100


def _chunks(items: list[str], size: int) -> list[list[str]]:
    """Split a list into fixed-size batches."""
    if size <= 0:
        return [items]
    return [items[i : i + size] for i in range(0, len(items), size)]


def get_azure_access_token(
    tenant_id: str,
    client_id: str,
    client_secret: str,
    scope: Optional[str] = None,
    timeout: int = 10,
) -> dict[str, Any]:
    """Acquire a Microsoft Entra ID access token using client credentials."""
    if not tenant_id or not client_id or not client_secret:
        return {
            "status": "error",
            "message": "Missing Azure credentials. Set AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET.",
        }

    token_scope = scope or f"{client_id}/.default"
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

    try:
        response = requests.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": token_scope,
            },
            timeout=timeout,
            verify=True,
        )

        if response.status_code != 200:
            return {
                "status": "error",
                "message": (
                    "Failed to acquire Entra token: "
                    f"HTTP {response.status_code}: {response.text[:300]}"
                ),
                "status_code": response.status_code,
            }

        payload = response.json()
        token = payload.get("access_token")
        if not token:
            return {"status": "error", "message": "Token response missing access_token."}

        return {
            "status": "success",
            "access_token": token,
            "token_type": payload.get("token_type", "Bearer"),
            "expires_in": payload.get("expires_in"),
            "scope": payload.get("scope") or token_scope,
            "message": "Azure access token obtained successfully.",
        }

    except requests.exceptions.Timeout:
        return {"status": "error", "message": f"Connection timeout after {timeout}s."}
    except requests.exceptions.ConnectionError as exc:
        return {"status": "error", "message": f"Connection error: {str(exc)[:300]}"}
    except Exception as exc:  # pragma: no cover - safety net
        return {"status": "error", "message": f"Unexpected error: {str(exc)[:300]}"}


def _ref_value(ref: Any) -> Optional[str]:
    """Return reference sys_id/value when field is encoded as dict or raw string."""
    if ref is None:
        return None
    if isinstance(ref, dict):
        value = ref.get("value")
        return str(value) if value not in (None, "") else None
    if isinstance(ref, str):
        return ref or None
    return str(ref)


def _ref_display(ref: Any) -> Optional[str]:
    """Return reference display text when field is encoded as dict or raw string."""
    if ref is None:
        return None
    if isinstance(ref, dict):
        display = ref.get("display_value")
        if display not in (None, ""):
            return str(display)
        value = ref.get("value")
        return str(value) if value not in (None, "") else None
    if isinstance(ref, str):
        return ref or None
    return str(ref)


def _normalise_max_records(max_records: Optional[int]) -> Optional[int]:
    if max_records is None:
        return None
    try:
        value = int(max_records)
    except (TypeError, ValueError):
        return None
    return None if value <= 0 else value


def _normalise_instance_url(instance_url: str) -> str:
    instance_url = instance_url.rstrip("/")
    if not instance_url.startswith("http"):
        instance_url = f"https://{instance_url}"
    return instance_url


def _build_auth_and_headers(
    auth_method: str,
    username: Optional[str],
    password: Optional[str],
    bearer_token: Optional[str],
) -> tuple[Optional[HTTPBasicAuth], dict[str, str], Optional[str]]:
    auth = None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}

    if auth_method == "basic":
        if not username or not password:
            return None, headers, "Username and password are required for basic auth."
        auth = HTTPBasicAuth(username, password)
    elif auth_method == "bearer":
        if not bearer_token:
            return None, headers, "Bearer token is required for API auth."
        headers["Authorization"] = f"Bearer {bearer_token}"
    else:
        return None, headers, f"Unsupported auth method: {auth_method}"

    return auth, headers, None


def _request_json(
    method: str,
    url: str,
    *,
    auth: Optional[HTTPBasicAuth],
    headers: dict[str, str],
    params: Optional[dict[str, Any]] = None,
    data: Optional[dict[str, Any]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    for attempt in range(3):
        response = None
        try:
            method = method.upper()
            if method == "GET":
                response = requests.get(
                    url,
                    auth=auth,
                    headers=headers,
                    params=params,
                    timeout=timeout,
                    verify=True,
                )
            elif method == "POST":
                response = requests.post(
                    url,
                    auth=auth,
                    headers=headers,
                    data=data,
                    timeout=timeout,
                    verify=True,
                )
            else:
                response = requests.request(
                    method,
                    url,
                    auth=auth,
                    headers=headers,
                    params=params,
                    data=data,
                    timeout=timeout,
                    verify=True,
                )

            if response.status_code == 401:
                return {
                    "status": "error",
                    "message": "Authentication failed. Check credentials/token and API roles.",
                    "status_code": 401,
                }

            if response.status_code == 404:
                return {
                    "status": "error",
                    "message": "Endpoint or table not found.",
                    "status_code": 404,
                }

            if response.status_code != 200:
                return {
                    "status": "error",
                    "message": f"HTTP {response.status_code}: {response.text[:300]}",
                    "status_code": response.status_code,
                }

            return {"status": "success", "payload": response.json()}

        except requests.exceptions.Timeout:
            if attempt == 2:
                return {
                    "status": "error",
                    "message": f"Connection timeout after {timeout}s. Check instance URL/network.",
                }
            time.sleep(2**attempt)
        except requests.exceptions.ConnectionError as exc:
            if attempt == 2:
                return {"status": "error", "message": f"Connection error: {str(exc)[:300]}"}
            time.sleep(2**attempt)
        except Exception as exc:  # pragma: no cover - safety net
            return {"status": "error", "message": f"Unexpected error: {str(exc)[:300]}"}
        finally:
            if response is not None:
                response.close()


def test_connection(
    instance_url: str,
    auth_method: str = "basic",
    username: Optional[str] = None,
    password: Optional[str] = None,
    bearer_token: Optional[str] = None,
    timeout: int = 10,
) -> dict[str, Any]:
    """Test ServiceNow connection and return a lightweight profile."""
    instance_url = _normalise_instance_url(instance_url)
    auth, headers, err = _build_auth_and_headers(
        auth_method, username, password, bearer_token
    )
    if err:
        return {"status": "error", "message": err}

    whoami_url = f"{instance_url}/api/now/table/sys_user"
    result = _request_json(
        "GET",
        whoami_url,
        auth=auth,
        headers=headers,
        params={
            "sysparm_limit": 1,
            "sysparm_exclude_reference_link": "true",
            "sysparm_fields": "sys_id,name,email,department",
        },
        timeout=timeout,
    )
    if result["status"] != "success":
        return result

    payload = result["payload"]
    user_info = payload.get("result", [{}])[0] if payload.get("result") else {}

    return {
        "status": "success",
        "instance_url": instance_url,
        "user_info": {
            "sys_id": user_info.get("sys_id"),
            "email": user_info.get("email"),
            "name": user_info.get("name"),
            "department": user_info.get("department"),
        },
        "message": f"Successfully connected as {user_info.get('name', 'Unknown')}",
    }


def query_table(
    instance_url: str,
    table_name: str,
    auth_method: str = "basic",
    username: Optional[str] = None,
    password: Optional[str] = None,
    bearer_token: Optional[str] = None,
    limit: int = 10,
    filters: Optional[dict[str, Any]] = None,
    timeout: int = 10,
    fields: Optional[list[str]] = None,
    order_by: Optional[str] = None,
    query: Optional[str] = None,
    offset: int = 0,
    display_value: str = "all",
) -> dict[str, Any]:
    """Query a ServiceNow table with optional filters, field projection, and ordering."""
    instance_url = _normalise_instance_url(instance_url)
    auth, headers, err = _build_auth_and_headers(
        auth_method, username, password, bearer_token
    )
    if err:
        return {"status": "error", "message": err}

    params: dict[str, Any] = {
        "sysparm_limit": limit,
        "sysparm_offset": max(offset, 0),
        "sysparm_exclude_reference_link": "true",
        "sysparm_display_value": display_value,
    }

    query_parts: list[str] = []
    if query:
        query_parts.append(query)
    if filters:
        for key, value in filters.items():
            query_parts.append(f"{key}={value}")
    if order_by:
        query_parts.append(f"ORDERBY{order_by}")
    if query_parts:
        params["sysparm_query"] = "^".join(query_parts)
    if fields:
        params["sysparm_fields"] = ",".join(fields)

    url = f"{instance_url}/api/now/table/{table_name}"
    result = _request_json(
        "GET",
        url,
        auth=auth,
        headers=headers,
        params=params,
        timeout=timeout,
    )
    if result["status"] != "success":
        status_code = result.get("status_code")
        if status_code == 404:
            return {"status": "error", "message": f"Table '{table_name}' not found."}
        return {"status": "error", "message": result.get("message", "Query failed")}

    records = result["payload"].get("result", [])
    return {
        "status": "success",
        "table": table_name,
        "records": records,
        "count": len(records),
        "message": f"Retrieved {len(records)} records from {table_name}",
        "next_offset": offset + len(records),
        "has_more": len(records) == limit,
    }


def fetch_all_records(
    instance_url: str,
    table_name: str,
    auth_method: str = "basic",
    username: Optional[str] = None,
    password: Optional[str] = None,
    bearer_token: Optional[str] = None,
    batch_size: int = 500,
    max_records: Optional[int] = None,
    filters: Optional[dict[str, Any]] = None,
    query: Optional[str] = None,
    fields: Optional[list[str]] = None,
    order_by: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Fetch records across pages until exhausted (or max_records reached)."""
    all_records: list[dict[str, Any]] = []
    offset = 0

    while True:
        if max_records is not None:
            remaining = max_records - len(all_records)
            if remaining <= 0:
                break
            page_limit = min(batch_size, remaining)
        else:
            page_limit = batch_size

        page = query_table(
            instance_url,
            table_name,
            auth_method=auth_method,
            username=username,
            password=password,
            bearer_token=bearer_token,
            limit=page_limit,
            filters=filters,
            timeout=timeout,
            fields=fields,
            order_by=order_by,
            query=query,
            offset=offset,
            display_value="all",
        )
        if page.get("status") != "success":
            return page

        records = page.get("records", [])
        all_records.extend(records)

        if len(records) < page_limit:
            break

        offset += len(records)

    return {
        "status": "success",
        "table": table_name,
        "records": all_records,
        "count": len(all_records),
        "message": f"Fetched {len(all_records)} records from {table_name}",
    }


LOCATION_FIELDS = ["sys_id", "name", "full_name", "parent"]
LOCATION_PARENT_HOP_LIMIT = 4


def _parse_location_region_territory(full_name: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Parse a cmn_location full_name breadcrumb, e.g. 'Global/Americas/UNITED STATES/...'.

    ServiceNow computes cmn_location.full_name as a '/'-joined ancestor path rooted at
    'Global'. Region is the segment right after Global; territory is the segment below
    that (typically country). Verified against live instance data on 2026-08-31.
    """
    if not full_name:
        return None, None
    parts = [p.strip() for p in full_name.split("/") if p.strip()]
    if len(parts) < 2 or parts[0].lower() != "global":
        return None, None
    return parts[1], (parts[2] if len(parts) > 2 else None)


def fetch_location_hierarchy(
    instance_url: str,
    *,
    location_ids: list[str],
    auth_method: str = "basic",
    username: Optional[str] = None,
    password: Optional[str] = None,
    bearer_token: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_hops: int = LOCATION_PARENT_HOP_LIMIT,
) -> dict[str, dict[str, Any]]:
    """Fetch cmn_location records plus enough ancestors to resolve region/territory.

    Some leaf locations (e.g. cloud datacenter records) have a full_name that hasn't
    been recomputed to include the geographic breadcrumb, even though their `parent`
    correctly points up the Region/Country hierarchy. This fetches each requested
    location and climbs `parent` links (in batch, one hop at a time) until every
    location has a resolvable ancestor or the hop limit is reached.
    """
    locations_by_id: dict[str, dict[str, Any]] = {}
    pending_ids = sorted({loc_id for loc_id in location_ids if loc_id})

    for _ in range(max_hops):
        pending_ids = [loc_id for loc_id in pending_ids if loc_id not in locations_by_id]
        if not pending_ids:
            break

        for batch in _chunks(pending_ids, CI_LOOKUP_BATCH_SIZE):
            result = fetch_all_records(
                instance_url,
                "cmn_location",
                auth_method=auth_method,
                username=username,
                password=password,
                bearer_token=bearer_token,
                max_records=len(batch),
                query=f"sys_idIN{','.join(batch)}",
                fields=LOCATION_FIELDS,
                timeout=timeout,
            )
            if result.get("status") != "success":
                continue
            for location in result.get("records", []):
                sys_id = _ref_value(location.get("sys_id"))
                if sys_id:
                    locations_by_id[sys_id] = location

        pending_ids = [
            _ref_value(location.get("parent"))
            for loc_id in pending_ids
            if (location := locations_by_id.get(loc_id))
        ]
        pending_ids = [loc_id for loc_id in pending_ids if loc_id]

    return locations_by_id


def resolve_region_territory(
    location_id: Optional[str],
    locations_by_id: dict[str, dict[str, Any]],
    max_hops: int = LOCATION_PARENT_HOP_LIMIT,
) -> tuple[Optional[str], Optional[str]]:
    """Walk up cmn_location.parent from location_id until full_name resolves."""
    seen: set[str] = set()
    current_id = location_id
    for _ in range(max_hops):
        if not current_id or current_id in seen:
            return None, None
        seen.add(current_id)
        location = locations_by_id.get(current_id)
        if not location:
            return None, None
        region, territory = _parse_location_region_territory(_ref_display(location.get("full_name")))
        if region:
            return region, territory
        current_id = _ref_value(location.get("parent"))
    return None, None


def fetch_customer_account_geography(
    instance_url: str,
    *,
    lob_group_ids: list[str],
    auth_method: str = "basic",
    username: Optional[str] = None,
    password: Optional[str] = None,
    bearer_token: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, dict[str, Any]]:
    """Resolve each CI's LOB group to its linked customer account (core_company) geography.

    Chain: cmdb_ci.u_lob (sys_user_group) -> u_customer_account (core_company) -> country/state/city.
    This reflects where the *customer* is based, distinct from where the CI is hosted.
    Verified against live instance data on 2026-08-31; matches the field chain used in the
    ServiceNow UI's manual Problem report ("Configuration item.LOB...contains United States/Canada").
    """
    if not lob_group_ids:
        return {}

    groups_by_id: dict[str, dict[str, Any]] = {}
    for batch in _chunks(sorted(set(lob_group_ids)), CI_LOOKUP_BATCH_SIZE):
        result = fetch_all_records(
            instance_url,
            "sys_user_group",
            auth_method=auth_method,
            username=username,
            password=password,
            bearer_token=bearer_token,
            max_records=len(batch),
            query=f"sys_idIN{','.join(batch)}",
            fields=["sys_id", "name", "u_customer_account"],
            timeout=timeout,
        )
        if result.get("status") != "success":
            continue
        for group in result.get("records", []):
            sys_id = _ref_value(group.get("sys_id"))
            if sys_id:
                groups_by_id[sys_id] = group

    account_ids = sorted(
        {_ref_value(group.get("u_customer_account")) for group in groups_by_id.values()} - {None}
    )
    accounts_by_id: dict[str, dict[str, Any]] = {}
    for batch in _chunks(account_ids, CI_LOOKUP_BATCH_SIZE):
        result = fetch_all_records(
            instance_url,
            "core_company",
            auth_method=auth_method,
            username=username,
            password=password,
            bearer_token=bearer_token,
            max_records=len(batch),
            query=f"sys_idIN{','.join(batch)}",
            fields=["sys_id", "name", "country", "state", "city"],
            timeout=timeout,
        )
        if result.get("status") != "success":
            continue
        for account in result.get("records", []):
            sys_id = _ref_value(account.get("sys_id"))
            if sys_id:
                accounts_by_id[sys_id] = account

    resolved: dict[str, dict[str, Any]] = {}
    for group_id, group in groups_by_id.items():
        account_id = _ref_value(group.get("u_customer_account"))
        account = accounts_by_id.get(account_id) if account_id else None
        resolved[group_id] = {
            "lob_name": _ref_display(group.get("name")),
            "account_sys_id": account_id,
            "account_name": _ref_display(account.get("name")) if account else None,
            "country": _ref_display(account.get("country")) if account else None,
            "state": _ref_display(account.get("state")) if account else None,
            "city": _ref_display(account.get("city")) if account else None,
        }
    return resolved


def pull_operational_snapshot(
    instance_url: str,
    auth_method: str = "basic",
    username: Optional[str] = None,
    password: Optional[str] = None,
    bearer_token: Optional[str] = None,
    since_days: int = 30,
    incident_max_records: Optional[int] = 5000,
    request_item_max_records: Optional[int] = 5000,
    include_change_requests: bool = False,
    include_problems: bool = False,
    change_request_max_records: Optional[int] = 3000,
    problem_max_records: Optional[int] = 3000,
    problem_ci_field: str = "cmdb_ci",
    timeout: int = DEFAULT_TIMEOUT,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    operational_query: Optional[str] = None,
) -> dict[str, Any]:
    """Pull incident and request-item snapshots for trend analysis.

    Includes active records as well as recently updated records so snapshots can
    measure backlog, not only tickets that changed during the lookback window.
    """
    since_days = max(int(since_days), 1)
    since_query = (
        "active=true^NQ"
        f"sys_updated_on>=javascript:gs.daysAgoStart({since_days})"
    )
    query = operational_query or since_query
    incident_max_records = _normalise_max_records(incident_max_records)
    request_item_max_records = _normalise_max_records(request_item_max_records)
    change_request_max_records = _normalise_max_records(change_request_max_records)
    problem_max_records = _normalise_max_records(problem_max_records)

    def report_progress(progress: float, message: str) -> None:
        if progress_callback:
            progress_callback(progress, message)

    report_progress(0.05, "Preparing ServiceNow snapshot queries...")

    incident_fields = [
        "sys_id",
        "number",
        "opened_at",
        "sys_created_on",
        "sys_updated_on",
        "resolved_at",
        "closed_at",
        "active",
        "state",
        "priority",
        "severity",
        "impact",
        "urgency",
        "category",
        "subcategory",
        "assignment_group",
        "assigned_to",
        "caller_id",
        "short_description",
    ]

    request_item_fields = [
        "sys_id",
        "number",
        "request",
        "opened_at",
        "sys_created_on",
        "sys_updated_on",
        "closed_at",
        "active",
        "state",
        "priority",
        "cat_item",
        "short_description",
        "assignment_group",
        "assigned_to",
        "requested_for",
    ]

    change_request_fields = [
        "sys_id",
        "number",
        "opened_at",
        "sys_created_on",
        "sys_updated_on",
        "start_date",
        "end_date",
        "state",
        "type",
        "risk",
        "priority",
        "category",
        "assignment_group",
        "assigned_to",
        "short_description",
    ]

    problem_fields = [
        "sys_id",
        "number",
        "opened_at",
        "sys_created_on",
        "sys_updated_on",
        "closed_at",
        "state",
        "priority",
        "known_error",
        "category",
        "cmdb_ci",
        "assignment_group",
        "assigned_to",
        "short_description",
    ]

    incidents = fetch_all_records(
        instance_url,
        "incident",
        auth_method=auth_method,
        username=username,
        password=password,
        bearer_token=bearer_token,
        max_records=incident_max_records,
        query=query,
        fields=incident_fields,
        order_by="sys_updated_on",
        timeout=timeout,
    )
    if incidents.get("status") != "success":
        return incidents
    report_progress(0.30, f"Incidents complete: {incidents.get('count', 0):,} records")

    request_items = fetch_all_records(
        instance_url,
        "sc_req_item",
        auth_method=auth_method,
        username=username,
        password=password,
        bearer_token=bearer_token,
        max_records=request_item_max_records,
        query=query,
        fields=request_item_fields,
        order_by="sys_updated_on",
        timeout=timeout,
    )
    if request_items.get("status") != "success":
        return request_items
    report_progress(0.55, f"Request items complete: {request_items.get('count', 0):,} records")

    change_requests: list[dict[str, Any]] = []
    if include_change_requests:
        changes = fetch_all_records(
            instance_url,
            "change_request",
            auth_method=auth_method,
            username=username,
            password=password,
            bearer_token=bearer_token,
            max_records=change_request_max_records,
            query=query,
            fields=change_request_fields,
            order_by="sys_updated_on",
            timeout=timeout,
        )
        if changes.get("status") != "success":
            return changes
        change_requests = changes["records"]
        report_progress(0.70, f"Change requests complete: {len(change_requests):,} records")

    problems: list[dict[str, Any]] = []
    if include_problems:
        problem_rows = fetch_all_records(
            instance_url,
            "problem",
            auth_method=auth_method,
            username=username,
            password=password,
            bearer_token=bearer_token,
            max_records=problem_max_records,
            query=query,
            fields=problem_fields,
            order_by="sys_updated_on",
            timeout=timeout,
        )
        if problem_rows.get("status") != "success":
            return problem_rows
        problems = problem_rows["records"]
        report_progress(0.82, f"Problems complete: {len(problems):,} records")

        for row in problems:
            ci_ref = row.get(problem_ci_field)
            row["cmdb_ci_sys_id"] = _ref_value(ci_ref)
            row["cmdb_ci_name"] = _ref_display(ci_ref)
            row["cmdb_ci_lob_sys_id"] = None
            row["cmdb_ci_lob"] = None
            row["cmdb_ci_lob_details_sys_id"] = None
            row["cmdb_ci_lob_details"] = None
            row["cmdb_ci_customer_relationship_sys_id"] = None
            row["cmdb_ci_customer_relationship"] = None
            row["cmdb_ci_customer_relationship_region"] = None
            row["cmdb_ci_customer_relationship_territory"] = None
            row["cmdb_ci_region"] = None
            row["cmdb_ci_territory"] = None
            row["cmdb_ci_region_source"] = None
            row["cmdb_ci_territory_source"] = None

        # cmdb_ci_region/territory come from the CI's *hosting* location: cmdb_ci.location ->
        # cmn_location.full_name, a 'Global/<Region>/<Country>/...' breadcrumb.
        # cmdb_ci_customer_relationship_region/territory come from the *customer's* account
        # geography: cmdb_ci.u_lob (sys_user_group) -> u_customer_account (core_company) ->
        # country/state. This matches the field chain used in the ServiceNow UI's manual
        # Problem report. Both verified against live instance data on 2026-08-31.
        ci_ids = sorted({row.get("cmdb_ci_sys_id") for row in problems if row.get("cmdb_ci_sys_id")})
        if ci_ids:
            ci_by_id: dict[str, dict[str, Any]] = {}
            for batch in _chunks(ci_ids, CI_LOOKUP_BATCH_SIZE):
                ci_rows = fetch_all_records(
                    instance_url,
                    "cmdb_ci",
                    auth_method=auth_method,
                    username=username,
                    password=password,
                    bearer_token=bearer_token,
                    max_records=len(batch),
                    query=f"sys_idIN{','.join(batch)}",
                    fields=["sys_id", "name", "location", "u_lob"],
                    timeout=timeout,
                )
                if ci_rows.get("status") != "success":
                    continue
                for ci in ci_rows.get("records", []):
                    sys_id = _ref_value(ci.get("sys_id"))
                    if sys_id:
                        ci_by_id[sys_id] = ci

            location_ids = [
                _ref_value(ci.get("location")) for ci in ci_by_id.values() if _ref_value(ci.get("location"))
            ]
            locations_by_id = fetch_location_hierarchy(
                instance_url,
                location_ids=location_ids,
                auth_method=auth_method,
                username=username,
                password=password,
                bearer_token=bearer_token,
                timeout=timeout,
            )

            lob_group_ids = [
                _ref_value(ci.get("u_lob")) for ci in ci_by_id.values() if _ref_value(ci.get("u_lob"))
            ]
            customer_geo_by_lob = fetch_customer_account_geography(
                instance_url,
                lob_group_ids=lob_group_ids,
                auth_method=auth_method,
                username=username,
                password=password,
                bearer_token=bearer_token,
                timeout=timeout,
            )

            for row in problems:
                ci_id = row.get("cmdb_ci_sys_id")
                if not ci_id:
                    continue
                ci = ci_by_id.get(ci_id)
                if not ci:
                    continue

                if not row.get("cmdb_ci_name"):
                    row["cmdb_ci_name"] = _ref_display(ci.get("name"))

                location_id = _ref_value(ci.get("location"))
                region, territory = resolve_region_territory(location_id, locations_by_id)
                if region:
                    row["cmdb_ci_region"] = region
                    row["cmdb_ci_region_source"] = "location_hierarchy"
                if territory:
                    row["cmdb_ci_territory"] = territory
                    row["cmdb_ci_territory_source"] = "location_hierarchy"

                lob_id = _ref_value(ci.get("u_lob"))
                lob_info = customer_geo_by_lob.get(lob_id) if lob_id else None
                if lob_info:
                    row["cmdb_ci_lob_sys_id"] = lob_id
                    row["cmdb_ci_lob"] = lob_info["lob_name"]
                    row["cmdb_ci_customer_relationship_sys_id"] = lob_info["account_sys_id"]
                    row["cmdb_ci_customer_relationship"] = lob_info["account_name"]
                    row["cmdb_ci_customer_relationship_region"] = lob_info["country"]
                    row["cmdb_ci_customer_relationship_territory"] = lob_info["state"]

        report_progress(0.92, "Problem CI enrichment complete")

    pulled_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    snapshot_date = datetime.utcnow().date().isoformat()
    report_progress(1.0, "Snapshot pull complete")

    return {
        "status": "success",
        "pulled_at": pulled_at,
        "snapshot_date": snapshot_date,
        "since_days": since_days,
        "incidents": incidents["records"],
        "request_items": request_items["records"],
        "change_requests": change_requests,
        "problems": problems,
        "incident_count": len(incidents["records"]),
        "request_item_count": len(request_items["records"]),
        "change_request_count": len(change_requests),
        "problem_count": len(problems),
        "include_change_requests": include_change_requests,
        "include_problems": include_problems,
        "message": (
            f"Pulled {len(incidents['records'])} incidents and "
            f"{len(request_items['records'])} request items"
            f"; {len(change_requests)} change requests"
            f"; {len(problems)} problems"
        ),
    }


def oauth_get_token(
    instance_url: str,
    client_id: str,
    client_secret: str,
    grant_type: str = "password",
    username: Optional[str] = None,
    password: Optional[str] = None,
    timeout: int = 10,
) -> dict[str, Any]:
    """Obtain an OAuth 2.0 access token from ServiceNow."""
    instance_url = _normalise_instance_url(instance_url)

    if grant_type not in ("password", "client_credentials"):
        return {"status": "error", "message": f"Unsupported grant_type: {grant_type}"}

    if grant_type == "password" and (not username or not password):
        return {
            "status": "error",
            "message": "Username and password are required for the password grant type.",
        }

    payload: dict[str, str] = {
        "grant_type": grant_type,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if grant_type == "password":
        payload["username"] = username  # type: ignore[assignment]
        payload["password"] = password  # type: ignore[assignment]

    try:
        response = requests.post(
            f"{instance_url}/oauth_token.do",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=timeout,
            verify=True,
        )

        if response.status_code == 401:
            return {
                "status": "error",
                "message": "OAuth authentication failed. Verify client_id, client_secret, and credentials.",
                "status_code": 401,
            }

        if response.status_code != 200:
            return {
                "status": "error",
                "message": f"OAuth token request failed: HTTP {response.status_code}: {response.text[:200]}",
                "status_code": response.status_code,
            }

        token_data = response.json()

        if "error" in token_data:
            return {
                "status": "error",
                "message": f"OAuth error - {token_data['error']}: {token_data.get('error_description', '')}",
            }

        return {
            "status": "success",
            "access_token": token_data.get("access_token"),
            "token_type": token_data.get("token_type", "Bearer"),
            "expires_in": token_data.get("expires_in"),
            "scope": token_data.get("scope"),
            "refresh_token": token_data.get("refresh_token"),
            "message": "OAuth token obtained successfully.",
        }

    except requests.exceptions.Timeout:
        return {"status": "error", "message": f"Connection timeout after {timeout}s."}
    except requests.exceptions.ConnectionError as exc:
        return {"status": "error", "message": f"Connection error: {str(exc)[:200]}"}
    except Exception as exc:  # pragma: no cover - safety net
        return {"status": "error", "message": f"Unexpected error: {str(exc)[:200]}"}


def oauth_refresh_token(
    instance_url: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    timeout: int = 10,
) -> dict[str, Any]:
    """Refresh an OAuth 2.0 access token using a refresh token."""
    instance_url = _normalise_instance_url(instance_url)

    try:
        response = requests.post(
            f"{instance_url}/oauth_token.do",
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=timeout,
            verify=True,
        )

        if response.status_code == 401:
            return {
                "status": "error",
                "message": "Token refresh failed: invalid or expired refresh token.",
                "status_code": 401,
            }

        if response.status_code != 200:
            return {
                "status": "error",
                "message": f"Token refresh failed: HTTP {response.status_code}: {response.text[:200]}",
                "status_code": response.status_code,
            }

        token_data = response.json()

        if "error" in token_data:
            return {
                "status": "error",
                "message": f"OAuth error - {token_data['error']}: {token_data.get('error_description', '')}",
            }

        return {
            "status": "success",
            "access_token": token_data.get("access_token"),
            "token_type": token_data.get("token_type", "Bearer"),
            "expires_in": token_data.get("expires_in"),
            "scope": token_data.get("scope"),
            "refresh_token": token_data.get("refresh_token"),
            "message": "OAuth token refreshed successfully.",
        }

    except requests.exceptions.Timeout:
        return {"status": "error", "message": f"Connection timeout after {timeout}s."}
    except requests.exceptions.ConnectionError as exc:
        return {"status": "error", "message": f"Connection error: {str(exc)[:200]}"}
    except Exception as exc:  # pragma: no cover - safety net
        return {"status": "error", "message": f"Unexpected error: {str(exc)[:200]}"}
