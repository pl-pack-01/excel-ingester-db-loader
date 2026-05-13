import os
import requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent / ".env")

INSTANCE_URL = os.environ["SN_INSTANCE_URL"].rstrip("/")
TENANT_ID = os.environ["AZURE_TENANT_ID"]
CLIENT_ID = os.environ["AZURE_CLIENT_ID"]
CLIENT_SECRET = os.environ["AZURE_CLIENT_SECRET"]
SCOPE = os.environ.get("AZURE_SCOPE") or f"{CLIENT_ID}/.default"

print(f"Instance:  {INSTANCE_URL}")
print(f"Tenant ID: {TENANT_ID}")
print(f"Client ID: {CLIENT_ID}")
print(f"Scope:     {SCOPE}")

# Step 1: Get token from Entra ID
print("\n--- Requesting Entra ID token ---")
token_url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
token_resp = requests.post(
    token_url,
    data={
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": SCOPE,
    },
)
print(f"Status: {token_resp.status_code}")
print(f"Response: {token_resp.text[:500]}")

if token_resp.status_code != 200:
    print("\nFailed to get token. Stopping.")
    exit(1)

token = token_resp.json()["access_token"]
print(f"\nToken obtained ({len(token)} chars): {token[:20]}...")

# Decode JWT payload (no signature check — just inspect claims)
import base64, json as _json
payload_b64 = token.split(".")[1]
payload_b64 += "=" * (4 - len(payload_b64) % 4)  # fix padding
claims = _json.loads(base64.b64decode(payload_b64))
print(f"Token audience (aud): {claims.get('aud')}")
print(f"Token issuer  (iss): {claims.get('iss')}")
print(f"App ID        (appid/azp): {claims.get('appid') or claims.get('azp')}")

# Step 2: Call ServiceNow API with the bearer token
print("\n--- Testing ServiceNow API access ---")
api_resp = requests.get(
    f"{INSTANCE_URL}/api/now/table/incident",
    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    params={"sysparm_limit": 1},
)
print(f"Status: {api_resp.status_code}")
print(f"Response: {api_resp.text[:500]}")