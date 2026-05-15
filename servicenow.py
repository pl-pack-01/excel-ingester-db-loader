"""ServiceNow REST API client with auth helpers and snapshot ingestion utilities."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import requests
from requests.auth import HTTPBasicAuth


DEFAULT_TIMEOUT = 20


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
        return {
            "status": "error",
            "message": f"Connection timeout after {timeout}s. Check instance URL/network.",
        }
    except requests.exceptions.ConnectionError as exc:
        return {"status": "error", "message": f"Connection error: {str(exc)[:300]}"}
    except Exception as exc:  # pragma: no cover - safety net
        return {"status": "error", "message": f"Unexpected error: {str(exc)[:300]}"}


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


def pull_operational_snapshot(
    instance_url: str,
    auth_method: str = "basic",
    username: Optional[str] = None,
    password: Optional[str] = None,
    bearer_token: Optional[str] = None,
    since_days: int = 30,
    incident_max_records: int = 5000,
    request_item_max_records: int = 5000,
    include_change_requests: bool = False,
    include_problems: bool = False,
    change_request_max_records: int = 3000,
    problem_max_records: int = 3000,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Pull incident and request-item snapshots for trend analysis.

    Uses `sys_updated_on>=javascript:gs.daysAgoStart(n)` so repeated runs produce
    daily snapshots that can be trended in SQLite.
    """
    since_days = max(int(since_days), 1)
    since_query = f"sys_updated_on>=javascript:gs.daysAgoStart({since_days})"

    incident_fields = [
        "sys_id",
        "number",
        "opened_at",
        "sys_created_on",
        "sys_updated_on",
        "resolved_at",
        "closed_at",
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
        query=since_query,
        fields=incident_fields,
        order_by="sys_updated_on",
        timeout=timeout,
    )
    if incidents.get("status") != "success":
        return incidents

    request_items = fetch_all_records(
        instance_url,
        "sc_req_item",
        auth_method=auth_method,
        username=username,
        password=password,
        bearer_token=bearer_token,
        max_records=request_item_max_records,
        query=since_query,
        fields=request_item_fields,
        order_by="sys_updated_on",
        timeout=timeout,
    )
    if request_items.get("status") != "success":
        return request_items

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
            query=since_query,
            fields=change_request_fields,
            order_by="sys_updated_on",
            timeout=timeout,
        )
        if changes.get("status") != "success":
            return changes
        change_requests = changes["records"]

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
            query=since_query,
            fields=problem_fields,
            order_by="sys_updated_on",
            timeout=timeout,
        )
        if problem_rows.get("status") != "success":
            return problem_rows
        problems = problem_rows["records"]

    pulled_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    snapshot_date = datetime.utcnow().date().isoformat()

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
