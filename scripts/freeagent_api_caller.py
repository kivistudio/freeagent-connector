"""NEVER DEPLOYED — a generic FreeAgent API caller, usable from the command line.

Two jobs. For anyone using this repo, it is the working half of the project: a way to read
a FreeAgent account from a terminal. For building the connector, it is how tool shapes get
designed against real data instead of guessed at, and how the undocumented parts of
FreeAgent's API get pinned down by asking rather than re-reading the docs.

**Usage, worked examples and the open-questions list live in
`.claude/skills/freeagent-api/SKILL.md`** — deliberately not duplicated here, because two
copies of the same guidance is how the copy in this file went stale.

============================ DO NOT SHIP THIS =================================
`request` must never be registered in `src/server.py`'s TOOL_MODULES.
The deployed server's safety rests on each tool exposing one endpoint with its IDs
validated by `SafeId`. A generic "call any endpoint" tool hands arbitrary API access to a
model that is reading FreeAgent data which may carry injected instructions, which undoes
that design completely. `tests/test_server.py` asserts this tool is absent from the
production server; do not weaken that test.
===============================================================================

It still routes through `FreeAgentClient`, so every call here exercises the same origin
guard as production — exploration reflects what the real server would do.
"""

from __future__ import annotations

import os
from typing import Any, Literal

import httpx
from fastmcp import FastMCP

from scripts.fa_auth import DEFAULT_API_BASE, basic_auth_header, read_env
from src.client import FreeAgentClient
from src.tools._helpers import freeagent_errors

API_CALLER_NAME = "freeagent-api-caller"

# The one tool name that must never appear on the production server.
GENERIC_REQUEST_TOOL = "request"

mcp: FastMCP = FastMCP(API_CALLER_NAME)


def _settings() -> dict[str, str]:
    """Read .env directly rather than relying on the environment.

    `fastmcp call` spawns this server as a subprocess, and exported shell variables do not
    reliably survive that. Reading the file is also what a user expects — no `source .env`
    incantation before every command.
    """
    return {**read_env(), **{k: v for k, v in os.environ.items() if k.startswith("FREEAGENT_")}}


def _dev_token() -> str:
    """Get a working FreeAgent access token.

    Prefers minting a fresh one from the refresh token: FreeAgent's access tokens last
    only an hour, so caching one turns the "one-time" browser flow into an hourly chore.
    The extra round trip is a fine price for never thinking about expiry again.
    """
    settings = _settings()
    api_base = settings.get("FREEAGENT_API_BASE_URL", DEFAULT_API_BASE).rstrip("/")
    refresh_token = settings.get("FREEAGENT_REFRESH_TOKEN")
    client_id = settings.get("FREEAGENT_CLIENT_ID")
    client_secret = settings.get("FREEAGENT_CLIENT_SECRET")

    if refresh_token and client_id and client_secret:
        response = httpx.post(
            f"{api_base}/token_endpoint",
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            headers={"Authorization": basic_auth_header(client_id, client_secret)},
            timeout=30.0,
        )
        if response.status_code == 200:
            return str(response.json()["access_token"])
        # Fall through to the stored access token; it may still be valid even if the
        # refresh grant was revoked.

    token = settings.get("FREEAGENT_DEV_TOKEN")
    if not token:
        raise RuntimeError(
            "No FreeAgent development credentials found in .env. Run "
            "`uv run scripts/fa_auth.py` to complete the OAuth flow once."
        )
    return token


def _describe_shape(value: Any, indent: int = 0) -> list[str]:
    """Summarise structure instead of dumping records.

    For designing a tool schema the useful question is which keys exist and what types
    they hold — not what one particular invoice happens to contain. Also keeps a
    thousand-row response from flooding the context.
    """
    pad = "  " * indent
    lines: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, dict):
                lines.append(f"{pad}{key}: object")
                lines.extend(_describe_shape(item, indent + 1))
            elif isinstance(item, list):
                lines.append(f"{pad}{key}: array[{len(item)}]")
                if item:
                    lines.extend(_describe_shape(item[0], indent + 1))
            else:
                lines.append(f"{pad}{key}: {type(item).__name__}")
    elif isinstance(value, list):
        lines.append(f"{pad}array[{len(value)}]")
        if value:
            lines.extend(_describe_shape(value[0], indent + 1))
    else:
        lines.append(f"{pad}{type(value).__name__}")
    return lines


@mcp.tool(name=GENERIC_REQUEST_TOOL)
async def request(
    path: str,
    method: Literal["GET", "POST", "PUT", "DELETE"] = "GET",
    params: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    show_headers: bool = False,
    shape_only: bool = False,
    confirm_write: bool = False,
) -> dict[str, Any]:
    """Call any FreeAgent API endpoint directly.

    Args:
        path: Relative API path, e.g. "/company" or "/bank_transactions".
        method: HTTP method. Anything other than GET requires confirm_write=True.
        params: Query string parameters, e.g. {"view": "unexplained", "per_page": "1"}.
        body: JSON request body, already wrapped in its resource key
            (e.g. {"contact": {...}}).
        show_headers: Include response headers in the result — X-Total-Count, Link,
            rate limit and content type.
        shape_only: Return an outline of the keys and their types instead of the values.
        confirm_write: Required for POST/PUT/DELETE. These modify real accounting
            records, so the flag has to be set deliberately.
    """
    if method != "GET" and not confirm_write:
        raise ValueError(
            f"{method} modifies real accounting data. Pass confirm_write=true only if "
            "you genuinely intend to change the books."
        )

    client = FreeAgentClient(
        token_provider=_dev_token,
        base_url=_settings().get("FREEAGENT_API_BASE_URL", DEFAULT_API_BASE),
    )

    with freeagent_errors():
        response = await client.request_raw(method, path, params=params, json_body=body)

    payload = response.json() if response.text else {}
    result: dict[str, Any] = {}

    if show_headers:
        interesting = ("X-Total-Count", "Link", "X-RateLimit-Remaining", "Content-Type")
        result["headers"] = {k: v for k, v in response.headers.items() if k.title() in interesting}

    result["data"] = "\n".join(_describe_shape(payload)) if shape_only else payload
    return result


if __name__ == "__main__":
    mcp.run()
