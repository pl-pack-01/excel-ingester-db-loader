"""Outlook integration via MSAL + Microsoft Graph API."""

import io
import msal
import requests


GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def build_msal_app(client_id: str, tenant_id: str) -> msal.PublicClientApplication:
    """Create an MSAL public client app for device-code or interactive auth."""
    authority = f"https://login.microsoftonline.com/{tenant_id}"
    return msal.PublicClientApplication(client_id, authority=authority)


def get_token_interactive(app: msal.PublicClientApplication, scopes: list[str]) -> str:
    """Acquire a token interactively (device code flow, works in headless/Streamlit)."""
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(scopes, account=accounts[0])
        if result and "access_token" in result:
            return result["access_token"]

    flow = app.initiate_device_flow(scopes=scopes)
    if "user_code" not in flow:
        raise RuntimeError(f"Device flow failed: {flow.get('error_description', 'unknown error')}")

    # Return the flow so the caller can display the user_code to the user
    return flow


def complete_device_flow(app: msal.PublicClientApplication, flow: dict) -> str:
    """Block until the user completes device-code auth. Returns access token."""
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" in result:
        return result["access_token"]
    raise RuntimeError(result.get("error_description", "Authentication failed"))


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def list_mail_folders(token: str) -> list[dict]:
    """Return all mail folders (id, displayName, childFolderCount)."""
    url = f"{GRAPH_BASE}/me/mailFolders?$top=100"
    folders = []
    while url:
        resp = requests.get(url, headers=_headers(token), timeout=30)
        resp.raise_for_status()
        data = resp.json()
        folders.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
    return folders


def list_child_folders(token: str, parent_id: str) -> list[dict]:
    """Return child folders under a given parent folder."""
    url = f"{GRAPH_BASE}/me/mailFolders/{parent_id}/childFolders?$top=100"
    folders = []
    while url:
        resp = requests.get(url, headers=_headers(token), timeout=30)
        resp.raise_for_status()
        data = resp.json()
        folders.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
    return folders


def list_messages_with_attachments(token: str, folder_id: str, top: int = 25) -> list[dict]:
    """Return recent messages that have attachments from a specific folder."""
    url = (
        f"{GRAPH_BASE}/me/mailFolders/{folder_id}/messages"
        f"?$filter=hasAttachments eq true"
        f"&$select=id,subject,from,receivedDateTime,hasAttachments"
        f"&$orderby=receivedDateTime desc"
        f"&$top={top}"
    )
    resp = requests.get(url, headers=_headers(token), timeout=30)
    resp.raise_for_status()
    return resp.json().get("value", [])


def list_attachments(token: str, message_id: str) -> list[dict]:
    """Return attachments for a message (id, name, contentType, size)."""
    url = f"{GRAPH_BASE}/me/messages/{message_id}/attachments"
    resp = requests.get(url, headers=_headers(token), timeout=30)
    resp.raise_for_status()
    return resp.json().get("value", [])


def download_attachment(token: str, message_id: str, attachment_id: str) -> tuple[str, bytes]:
    """Download a specific attachment. Returns (filename, content_bytes)."""
    url = f"{GRAPH_BASE}/me/messages/{message_id}/attachments/{attachment_id}"
    resp = requests.get(url, headers=_headers(token), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    import base64
    content = base64.b64decode(data["contentBytes"])
    return data["name"], content
