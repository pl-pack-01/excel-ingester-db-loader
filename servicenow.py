"""ServiceNow REST API client with optional bearer token auth."""

from typing import Any, Optional

import requests
from requests.auth import HTTPBasicAuth


def test_connection(
    instance_url: str,
    auth_method: str = "basic",
    username: Optional[str] = None,
    password: Optional[str] = None,
    bearer_token: Optional[str] = None,
    timeout: int = 10,
) -> dict[str, Any]:
    """
    Test ServiceNow connection and return instance info.
    
    Args:
        instance_url: ServiceNow instance URL (e.g., https://dev12345.service-now.com)
        auth_method: Authentication type, either "basic" or "bearer"
        username: Username for basic auth
        password: Password for basic auth
        bearer_token: Bearer token for API auth
        timeout: Request timeout in seconds
    
    Returns:
        Dict with status, user_info, and accessible tables.
    """
    # Normalize URL
    instance_url = instance_url.rstrip("/")
    if not instance_url.startswith("http"):
        instance_url = f"https://{instance_url}"
    
    try:
        # Test connection to whoami endpoint
        whoami_url = f"{instance_url}/api/now/v2/table/sys_user?sysparm_limit=1"
        auth = None
        headers = {"Content-Type": "application/json"}

        if auth_method == "basic":
            if not username or not password:
                return {"status": "error", "message": "Username and password are required for basic auth."}
            auth = HTTPBasicAuth(username, password)
        elif auth_method == "bearer":
            if not bearer_token:
                return {"status": "error", "message": "Bearer token is required for API auth."}
            headers["Authorization"] = f"Bearer {bearer_token}"
        else:
            return {"status": "error", "message": f"Unsupported auth method: {auth_method}"}
        
        response = requests.get(
            whoami_url,
            auth=auth,
            headers=headers,
            timeout=timeout,
            verify=True,
        )
        
        if response.status_code == 401:
            return {
                "status": "error",
                "message": "Authentication failed. Check credentials.",
                "status_code": 401,
            }
        
        if response.status_code != 200:
            return {
                "status": "error",
                "message": f"HTTP {response.status_code}: {response.text[:200]}",
                "status_code": response.status_code,
            }
        
        # Get user info
        data = response.json()
        user_info = data.get("result", [{}])[0] if data.get("result") else {}
        
        # Try to get list of accessible tables
        tables_response = requests.get(
            f"{instance_url}/api/now/v1/ui/aggregates",
            auth=auth,
            headers=headers,
            timeout=timeout,
            verify=True,
        )
        
        accessible_tables = []
        if tables_response.status_code == 200:
            tables_data = tables_response.json()
            accessible_tables = tables_data.get("result", [])[:10]  # First 10
        
        return {
            "status": "success",
            "instance_url": instance_url,
            "user_info": {
                "sys_id": user_info.get("sys_id"),
                "email": user_info.get("email"),
                "name": user_info.get("name"),
                "department": user_info.get("department"),
            },
            "accessible_tables": accessible_tables,
            "message": f"Successfully connected as {user_info.get('name', 'Unknown')}",
        }
    
    except requests.exceptions.Timeout:
        return {
            "status": "error",
            "message": f"Connection timeout after {timeout}s. Check instance URL.",
        }
    except requests.exceptions.ConnectionError as e:
        return {
            "status": "error",
            "message": f"Connection error: {str(e)[:200]}",
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Unexpected error: {str(e)[:200]}",
        }


def query_table(
    instance_url: str,
    table_name: str,
    auth_method: str = "basic",
    username: Optional[str] = None,
    password: Optional[str] = None,
    bearer_token: Optional[str] = None,
    limit: int = 10,
    filters: Optional[dict] = None,
    timeout: int = 10,
) -> dict[str, Any]:
    """
    Query a ServiceNow table.
    
    Args:
        instance_url: ServiceNow instance URL
        table_name: Name of the table to query (e.g., 'incident', 'change_request')
        auth_method: Authentication type, either "basic" or "bearer"
        username: Username for basic auth
        password: Password for basic auth
        bearer_token: Bearer token for API auth
        limit: Max records to return
        filters: Optional dict of field filters {field_name: value}
        timeout: Request timeout in seconds
    
    Returns:
        Dict with status, records, and total count.
    """
    instance_url = instance_url.rstrip("/")
    if not instance_url.startswith("http"):
        instance_url = f"https://{instance_url}"
    
    try:
        url = f"{instance_url}/api/now/table/{table_name}"
        auth = None
        headers = {"Content-Type": "application/json"}

        if auth_method == "basic":
            if not username or not password:
                return {"status": "error", "message": "Username and password are required for basic auth."}
            auth = HTTPBasicAuth(username, password)
        elif auth_method == "bearer":
            if not bearer_token:
                return {"status": "error", "message": "Bearer token is required for API auth."}
            headers["Authorization"] = f"Bearer {bearer_token}"
        else:
            return {"status": "error", "message": f"Unsupported auth method: {auth_method}"}
        
        params = {
            "sysparm_limit": limit,
            "sysparm_exclude_reference_link": "true",
        }
        
        # Add filters if provided
        if filters:
            query_parts = []
            for key, value in filters.items():
                query_parts.append(f"{key}={value}")
            if query_parts:
                params["sysparm_query"] = "^".join(query_parts)
        
        response = requests.get(
            url,
            auth=auth,
            headers=headers,
            params=params,
            timeout=timeout,
            verify=True,
        )
        
        if response.status_code == 401:
            return {"status": "error", "message": "Authentication failed."}
        
        if response.status_code == 404:
            return {"status": "error", "message": f"Table '{table_name}' not found."}
        
        if response.status_code != 200:
            return {"status": "error", "message": f"HTTP {response.status_code}: {response.text[:200]}"}
        
        data = response.json()
        records = data.get("result", [])
        
        return {
            "status": "success",
            "table": table_name,
            "records": records,
            "count": len(records),
            "message": f"Retrieved {len(records)} records from {table_name}",
        }
    
    except Exception as e:
        return {"status": "error", "message": f"Query failed: {str(e)[:200]}"}


def oauth_get_token(
    instance_url: str,
    client_id: str,
    client_secret: str,
    grant_type: str = "password",
    username: Optional[str] = None,
    password: Optional[str] = None,
    timeout: int = 10,
) -> dict[str, Any]:
    """
    Obtain an OAuth 2.0 access token from ServiceNow.

    Supports grant_type='password' (Resource Owner Password Credentials) and
    grant_type='client_credentials'.

    Args:
        instance_url: ServiceNow instance URL (e.g., https://dev12345.service-now.com)
        client_id: OAuth client ID registered in ServiceNow
        client_secret: OAuth client secret
        grant_type: OAuth grant type – 'password' or 'client_credentials'
        username: User login (required for 'password' grant)
        password: User password (required for 'password' grant)
        timeout: Request timeout in seconds

    Returns:
        Dict with status, access_token, token_type, expires_in, scope, and
        refresh_token (password grant only).
    """
    instance_url = instance_url.rstrip("/")
    if not instance_url.startswith("http"):
        instance_url = f"https://{instance_url}"

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
                "message": f"OAuth error – {token_data['error']}: {token_data.get('error_description', '')}",
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
    except requests.exceptions.ConnectionError as e:
        return {"status": "error", "message": f"Connection error: {str(e)[:200]}"}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {str(e)[:200]}"}


def oauth_refresh_token(
    instance_url: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    timeout: int = 10,
) -> dict[str, Any]:
    """
    Refresh an OAuth 2.0 access token using a refresh token.

    Args:
        instance_url: ServiceNow instance URL
        client_id: OAuth client ID
        client_secret: OAuth client secret
        refresh_token: The refresh token obtained from a previous token grant
        timeout: Request timeout in seconds

    Returns:
        Dict with status, access_token, token_type, expires_in, scope, and
        new refresh_token.
    """
    instance_url = instance_url.rstrip("/")
    if not instance_url.startswith("http"):
        instance_url = f"https://{instance_url}"

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
                "message": f"OAuth error – {token_data['error']}: {token_data.get('error_description', '')}",
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
    except requests.exceptions.ConnectionError as e:
        return {"status": "error", "message": f"Connection error: {str(e)[:200]}"}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {str(e)[:200]}"}
