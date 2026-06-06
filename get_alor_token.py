#!/usr/bin/env python3
"""Get Alor API token via OAuth2 client credentials."""
import requests, json, sys
from pathlib import Path

# Load .env
env_path = Path(__file__).parent / ".env"
env_vars = {}
if env_path.exists():
    for line in env_path.read_text().strip().split("\n"):
        if "=" in line:
            k, v = line.split("=", 1)
            env_vars[k.strip()] = v.strip()

CLIENT_ID = env_vars.get("ALOR_Client_ID", "")
CLIENT_SECRET = env_vars.get("ALOR_Client_Secret", "")

if not CLIENT_ID or not CLIENT_SECRET:
    print("ERROR: No ALOR_Client_ID or ALOR_Client_Secret in .env")
    sys.exit(1)

print(f"Client ID: {CLIENT_ID[:10]}...")

# Try Alor OAuth2 token endpoint
# Common endpoints for Alor:
endpoints = [
    "https://api.alor.ru/oauth2/token",
    "https://oauth.alor.ru/token",
    "https://login.alor.ru/connect/token",
    "https://api.alor.ru/auth/oauth2/token",
]

for url in endpoints:
    print(f"\nTrying: {url}")
    try:
        data = {
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        }
        resp = requests.post(url, data=data, timeout=15, 
                           headers={"Content-Type": "application/x-www-form-urlencoded"})
        print(f"  HTTP {resp.status_code}")
        if resp.status_code == 200:
            body = resp.json()
            print(f"  Token: {body.get('access_token', 'N/A')[:40]}...")
            print(f"  Type: {body.get('token_type', 'N/A')}")
            print(f"  Scope: {body.get('scope', 'N/A')}")
            print(f"  Expires: {body.get('expires_in', 'N/A')}s")
            print(f"\n  FULL RESPONSE:")
            print(json.dumps(body, indent=2)[:500])
        else:
            print(f"  {resp.text[:200]}")
    except Exception as e:
        print(f"  Error: {e}")

# Also try with basic auth header
print("\n\n=== Try with Basic Auth header ===")
import base64
basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
for url in endpoints:
    print(f"\nTrying (basic): {url}")
    try:
        data = {"grant_type": "client_credentials"}
        headers = {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        resp = requests.post(url, data=data, headers=headers, timeout=15)
        print(f"  HTTP {resp.status_code}")
        if resp.status_code == 200:
            body = resp.json()
            print(f"  Token: {body.get('access_token', 'N/A')[:40]}...")
            print(f"  Scope: {body.get('scope', 'N/A')}")
            print(f"\n  {json.dumps(body, indent=2)[:500]}")
        else:
            print(f"  {resp.text[:200]}")
    except Exception as e:
        print(f"  Error: {e}")
