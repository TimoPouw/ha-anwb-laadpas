#!/usr/bin/env python3
"""Probe the ANWB Laadpas (Deftpower) API before building the integration.

Standard library only, so it runs with any Python 3.10+ without installing anything.

Two modes:

  python tools/probe.py refresh
      Reads a refresh token from ANWB_REFRESH_TOKEN (environment or .env in the
      repo root), refreshes it, fetches one page of completed charging sessions
      and writes the raw response to tools/sample_response.json.

  python tools/probe.py login
      Tests the "login via URL" flow: generates PKCE + state, prints the
      authorize URL, asks you to paste the URL you end up on after logging in
      (it contains ?code=...), exchanges the code and then does the same fetch.

Because the identity server may rotate refresh tokens, the newest refresh token
is always written back to .env (gitignored). The token you copied from the
browser may stop working after the first refresh.

Outputs (both gitignored, they contain real personal data):
  tools/sample_response.json   raw API response
  tools/probe_report.txt       field names and types only, no values; safe to share
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

IDP = "https://account.deftpower.com"
TOKEN_URL = f"{IDP}/connect/token"
DISCOVERY_URL = f"{IDP}/.well-known/openid-configuration"
CLIENT_ID = "35b611a4-67cc-47f9-8f49-36218ac72877"
REDIRECT_URI = "https://laadpas.anwb.nl/account/login"
SCOPE = (
    "openid identity backend pricing swagger statistics plugandcharge "
    "energy-price offline_access"
)
TENANT_ID = "468e5c33-2b83-42f6-a623-a7305e77dcc0"
ACR_VALUES = f"tenant:{TENANT_ID} autoRedirect:true"
SESSIONS_URL = (
    "https://portal-api.deftpower.com/api/services/app/ChargingSessions/GetAll"
)
PAGE_SIZE = int(os.environ.get("ANWB_PAGE_SIZE", "10"))

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
SAMPLE_FILE = ROOT / "tools" / "sample_response.json"
REPORT_FILE = ROOT / "tools" / "probe_report.txt"
USER_AGENT = "ha-anwb-laadpas-probe/0.1"

report: list[str] = []


def log(line: str = "") -> None:
    print(line)
    report.append(line)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text().splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def save_refresh_token(token: str) -> None:
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    lines = [ln for ln in lines if not ln.startswith("ANWB_REFRESH_TOKEN=")]
    lines.append(f"ANWB_REFRESH_TOKEN={token}")
    ENV_FILE.write_text("\n".join(lines) + "\n")
    ENV_FILE.chmod(0o600)


def http(
    method: str,
    url: str,
    *,
    data: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    for key, val in (headers or {}).items():
        req.add_header(key, val)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()
    except urllib.error.URLError as err:
        sys.exit(f"Network error calling {url.split('?')[0]}: {err.reason}")


def jwt_claims(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        return {}


def describe(value: Any, indent: int = 0, max_depth: int = 6) -> list[str]:
    """Describe the shape of a JSON value: keys and types, never the values."""
    pad = "  " * indent
    out: list[str] = []
    if isinstance(value, dict):
        for key, val in value.items():
            if isinstance(val, (dict, list)) and indent < max_depth:
                kind = "object" if isinstance(val, dict) else f"array[{len(val)}]"
                out.append(f"{pad}{key}: {kind}")
                out.extend(describe(val, indent + 1, max_depth))
            else:
                out.append(f"{pad}{key}: {type_hint(val)}")
    elif isinstance(value, list) and value:
        out.append(f"{pad}[0]:")
        out.extend(describe(value[0], indent + 1, max_depth))
    return out


def type_hint(val: Any) -> str:
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "bool"
    if isinstance(val, int):
        return "int"
    if isinstance(val, float):
        return "float"
    if isinstance(val, str):
        # Give a hint about the format without leaking the value.
        if len(val) >= 19 and val[4] == "-" and val[10] in "T ":
            return f"str (datetime, e.g. format {fmt_mask(val)})"
        return f"str (len {len(val)})"
    return type(val).__name__


def fmt_mask(val: str) -> str:
    return "".join("9" if c.isdigit() else c for c in val)


# --------------------------------------------------------------------------- #
# Token handling
# --------------------------------------------------------------------------- #
def token_request(data: dict[str, str], label: str) -> dict[str, Any]:
    status, _, body = http("POST", TOKEN_URL, data=data)
    log(f"[{label}] token endpoint -> HTTP {status}")
    try:
        payload = json.loads(body)
    except ValueError:
        log(f"[{label}] non-JSON response: {body[:300]!r}")
        sys.exit(1)
    if status != 200:
        # Error responses from the IdP contain no secrets, show them.
        log(f"[{label}] error: {json.dumps(payload)}")
        sys.exit(1)

    log(f"[{label}] response keys: {sorted(payload)}")
    log(f"[{label}] expires_in: {payload.get('expires_in')}")
    log(f"[{label}] scope: {payload.get('scope')}")
    claims = jwt_claims(payload.get("access_token", ""))
    if claims:
        log(f"[{label}] access token claim names: {sorted(claims)}")
        for key in ("tenant", "tenant_id", "tenantId", "client_id", "amr", "idp"):
            if key in claims:
                log(f"[{label}] claim {key} = {claims[key]}")
    return payload


def do_refresh(refresh_token: str) -> str:
    payload = token_request(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
        },
        "refresh",
    )
    new_rt = payload.get("refresh_token")
    if new_rt:
        log(f"[refresh] refresh token rotated: {new_rt != refresh_token}")
        save_refresh_token(new_rt)
        log("[refresh] newest refresh token saved to .env")
    else:
        log("[refresh] no new refresh token returned (no rotation)")
    return payload["access_token"]


def do_login() -> str:
    status, _, body = http("GET", DISCOVERY_URL)
    discovery = json.loads(body) if status == 200 else {}
    authorize_url = discovery.get("authorization_endpoint", f"{IDP}/connect/authorize")
    log(f"[login] authorize endpoint: {authorize_url}")
    log(f"[login] PKCE methods: {discovery.get('code_challenge_methods_supported')}")

    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    state = secrets.token_urlsafe(24)
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "state": state,
        "nonce": secrets.token_urlsafe(24),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "acr_values": ACR_VALUES,
        "response_mode": "query",
    }
    url = f"{authorize_url}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"

    print()
    print("1. Open this URL in a browser and log in (including MFA):")
    print()
    print(url)
    print()
    print("2. After login you land on laadpas.anwb.nl/account/login?code=...")
    print("   Copy that full URL from the address bar as fast as possible.")
    print("   If the page redirects away before you can copy it: open DevTools,")
    print("   Network tab, enable 'Preserve log', repeat, and copy the URL of the")
    print("   request to /account/login?code=... from there.")
    print()
    pasted = input("3. Paste the URL here: ").strip()

    query = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query)
    if "error" in query:
        log(f"[login] IdP returned error: {query.get('error')} {query.get('error_description')}")
        sys.exit(1)
    if query.get("state", [None])[0] != state:
        log("[login] state mismatch, aborting")
        sys.exit(1)
    code = query.get("code", [None])[0]
    if not code:
        log("[login] no code found in pasted URL")
        sys.exit(1)

    payload = token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
            "client_id": CLIENT_ID,
        },
        "login",
    )
    log("[login] code exchange WORKS: login via URL is viable")
    if payload.get("refresh_token"):
        save_refresh_token(payload["refresh_token"])
        log("[login] refresh token saved to .env")
    else:
        log("[login] WARNING: no refresh token returned (offline_access missing?)")
    return payload["access_token"]


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def fetch_sessions(access_token: str) -> None:
    params = {
        "SessionStatusFilter": "COMPLETED",
        "IncludeTotalCount": "true",
        "Sorting": "startDateTime DESC",
        "SkipCount": "0",
        "MaxResultCount": str(PAGE_SIZE),
    }
    url = f"{SESSIONS_URL}?{urllib.parse.urlencode(params)}"
    variants: list[tuple[str, dict[str, str]]] = [
        ("no tenant header", {}),
        ("Abp-TenantId", {"Abp-TenantId": TENANT_ID}),
        ("Abp.TenantId", {"Abp.TenantId": TENANT_ID}),
    ]

    winner: bytes | None = None
    for label, extra in variants:
        headers = {"Authorization": f"Bearer {access_token}", **extra}
        status, _, body = http("GET", url, headers=headers)
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = None
        success = parsed.get("success") if isinstance(parsed, dict) else None
        result = parsed.get("result") if isinstance(parsed, dict) else None
        count = None
        if isinstance(result, dict):
            items = result.get("items")
            count = len(items) if isinstance(items, list) else None
        log(f"[sessions] {label:16} -> HTTP {status}, success={success}, items={count}")
        if status != 200 and isinstance(parsed, dict) and parsed.get("error"):
            log(f"[sessions]   error: {json.dumps(parsed['error'])[:300]}")
        if status == 200 and success and winner is None:
            winner = body

    if winner is None:
        log("[sessions] no variant succeeded, nothing written")
        sys.exit(1)

    data = json.loads(winner)
    SAMPLE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    log(f"[sessions] raw response written to {SAMPLE_FILE.relative_to(ROOT)}")
    log()
    log("Response shape (keys and types only, no values):")
    log("\n".join(describe(data)))

    items = (data.get("result") or {}).get("items") or []
    if items:
        all_keys: dict[str, set[str]] = {}
        for item in items:
            for key, val in item.items():
                all_keys.setdefault(key, set()).add(type_hint(val).split(" ")[0])
        log()
        log(f"Session field types across all {len(items)} items:")
        for key, kinds in all_keys.items():
            log(f"  {key}: {', '.join(sorted(kinds))}")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "refresh"
    if mode == "login":
        access_token = do_login()
    elif mode == "refresh":
        token = os.environ.get("ANWB_REFRESH_TOKEN") or load_env().get("ANWB_REFRESH_TOKEN")
        if not token:
            sys.exit("Set ANWB_REFRESH_TOKEN in the environment or in .env first.")
        access_token = do_refresh(token)
    else:
        sys.exit(__doc__)

    fetch_sessions(access_token)
    REPORT_FILE.write_text("\n".join(report) + "\n")
    print()
    print(f"Shareable report (no values) written to {REPORT_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
