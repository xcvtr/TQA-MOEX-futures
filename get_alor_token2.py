#!/usr/bin/env python3
"""Try different OAuth2 grant types on Alor token endpoint."""
import requests, json, sys, base64
from pathlib import Path

env_path = Path(__file__).parent / ".env"
env_vars = {}
if env_path.exists():
    for line in env_path.read_text().split("\n"):
        line = line.strip()
        if "=" in line:
            k, v = line.split("=", 1)
            env_vars[k.strip()] = v.strip()

CLIENT_ID = env_vars.get("ALOR_Client_ID", "")
CLIENT_SECRET = env_vars.get("ALOR_Client_Secret", "")
TOKEN_URL = "https://oauth.alor.ru/token"

if not CLIENT_ID or not CLIENT_SECRET:
    print("ERROR: No credentials found")
    sys.exit(1)

basic = base64.b64encode((CLIENT_ID + ":" + CLIENT_SECRET).encode()).decode()
print("Client ID:", CLIENT_ID[:12] + "...")
print("Secret:", CLIENT_SECRET[:10] + "...")
print()

# Try client_credentials with different scopes
for scope in ["read", "trade", "orders", "read trade", "read orders trade", "all", "trading", "marketdata"]:
    print("--- client_credentials scope=" + scope + " ---")
    data = {"grant_type": "client_credentials", "scope": scope}
    headers = {
        "Authorization": "Basic " + basic,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        resp = requests.post(TOKEN_URL, data=data, headers=headers, timeout=15)
        print("  HTTP", resp.status_code)
        if resp.status_code == 200:
            body = resp.json()
            token = body.get("access_token", "")
            print("  TOKEN:", token[:50] + "...")
            print("  Scope:", body.get("scope", "N/A"))
            with open(Path(__file__).parent / "trade_token.txt", "w") as f:
                f.write(token)
            print("  SAVED to trade_token.txt")
        else:
            print("  " + resp.text[:200])
    except Exception as e:
        print("  Error:", e)
    print()

# Try refresh_token grant
print("--- refresh_token with existing JWT ---")
existing = "255375ae-88fa-4f33-bedd-6d9f6a432370"
try:
    resp = requests.post(TOKEN_URL, data={
        "grant_type": "refresh_token",
        "refresh_token": existing,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }, timeout=15)
    print("  HTTP", resp.status_code, ":", resp.text[:200])
except Exception as e:
    print("  Error:", e)
