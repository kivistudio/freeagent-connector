"""One-time FreeAgent OAuth login, for the command-line API caller.

FreeAgent only issues tokens through the authorization-code flow, so there is no way to
get a development token without a browser round trip. This does that once and writes the
result to `.env`, after which `scripts/freeagent_api_caller.py` works offline from any consent
screen.

    uv run scripts/fa_auth.py

The deployed connector does not use this at all — `OAuthProxy` owns that flow.
"""

from __future__ import annotations

import base64
import http.server
import re
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"
DEFAULT_REDIRECT_URI = "http://localhost:8723/callback"
DEFAULT_API_BASE = "https://api.freeagent.com/v2"


def read_env() -> dict[str, str]:
    """Parse .env without taking a dependency on python-dotenv."""
    values: dict[str, str] = {}
    if not ENV_PATH.exists():
        return values
    for raw in ENV_PATH.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env_value(key: str, value: str) -> None:
    """Set a key in .env, replacing any existing entry."""
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    for i, line in enumerate(lines):
        if re.match(rf"^\s*#?\s*{re.escape(key)}\s*=", line):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n")


def basic_auth_header(client_id: str, client_secret: str) -> str:
    return "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    code: str | None = None

    def do_GET(self) -> None:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        codes = params.get("code")
        _CallbackHandler.code = codes[0] if codes else None
        body = (
            b"<h1>Authorized</h1><p>Close this tab and return to the terminal.</p>"
            if _CallbackHandler.code
            else b"<h1>No authorization code received</h1>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        """Silence default request logging."""


def main() -> None:
    env = read_env()
    client_id = env.get("FREEAGENT_CLIENT_ID")
    client_secret = env.get("FREEAGENT_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit(
            f"Set FREEAGENT_CLIENT_ID and FREEAGENT_CLIENT_SECRET in {ENV_PATH} first "
            "(copy .env.example)."
        )

    redirect_uri = env.get("FREEAGENT_REDIRECT_URI", DEFAULT_REDIRECT_URI)
    api_base = env.get("FREEAGENT_API_BASE_URL", DEFAULT_API_BASE).rstrip("/")
    port = urllib.parse.urlparse(redirect_uri).port or 80

    query = urllib.parse.urlencode(
        {"client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri}
    )
    authorize_url = f"{api_base}/approve_app?{query}"

    print(f"Redirect URI: {redirect_uri}")
    print("This must EXACTLY match the one registered on your FreeAgent OAuth app.\n")

    server = http.server.HTTPServer(("localhost", port), _CallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    print("Opening your browser to approve access...")
    print(f"If it does not open, visit:\n{authorize_url}\n")
    webbrowser.open(authorize_url)

    thread.join(timeout=300)
    server.server_close()

    if not _CallbackHandler.code:
        sys.exit("No authorization code received (timed out after 5 minutes).")

    response = httpx.post(
        f"{api_base}/token_endpoint",
        data={
            "grant_type": "authorization_code",
            "code": _CallbackHandler.code,
            "redirect_uri": redirect_uri,
        },
        headers={"Authorization": basic_auth_header(client_id, client_secret)},
        timeout=30.0,
    )
    if response.status_code != 200:
        sys.exit(f"Token exchange failed ({response.status_code}): {response.text[:300]}")

    payload = response.json()
    access = payload.get("access_token")
    if not access:
        sys.exit("FreeAgent returned no access token.")

    write_env_value("FREEAGENT_DEV_TOKEN", access)
    if payload.get("refresh_token"):
        write_env_value("FREEAGENT_REFRESH_TOKEN", payload["refresh_token"])

    expires = payload.get("expires_in")
    print(f"Token written to {ENV_PATH}.")
    if expires:
        print(f"It expires in {expires}s — re-run this script when calls start 401ing.")
    print("Now try: uv run fastmcp call scripts/freeagent_api_caller.py request path=/company")


if __name__ == "__main__":
    main()
