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
