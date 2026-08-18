"""FastMCP server for the FreeAgent connector.

`create_server()` is the single, transport-agnostic factory used everywhere. It assembles
the configured server (auth, FreeAgent client, tools, /health) but never opens a socket —
so "local" vs "remote" is decided by how the returned object is run and which env vars are
set, not by a different builder:

- deployed / remote: `main()` runs it over HTTP inside the Scaleway container.
- tests: import it and attach an in-memory / ASGI transport, no socket.
- local dev: `fastmcp dev` loads it; FREEAGENT_DEV_TOKEN swaps the OAuth flow for a token.

This is the connector in `src/`; the always-local command-line caller lives in `scripts/`
and is never deployed.
"""

from __future__ import annotations

import os

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware.logging import StructuredLoggingMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.auth import FREEAGENT_API_BASE_URL, build_auth_provider
from src.client import FreeAgentClient
from src.log import logger
from src.tools import company

SERVICE_NAME = "freeagent-mcp-remote"

# Tool modules registered on the server. Adding a resource module means adding it here.
TOOL_MODULES = (company,)


def freeagent_token_provider() -> str:
    """Return the FreeAgent bearer token for the request currently being handled.

    Normally this is the upstream token OAuthProxy is holding for this client, fetched
    per-call so its transparent refresh is picked up.

    FREEAGENT_DEV_TOKEN is a local-development escape hatch: OAuthProxy's browser consent
    flow is awkward to iterate against, so setting that variable lets `fastmcp dev
    inspector --auth none` talk to real FreeAgent data. It is opt-in, absent from the
    deployed container's environment, and there is a test asserting it does nothing
    unless explicitly set. Do not set it in production.
    """
    dev_token = os.environ.get("FREEAGENT_DEV_TOKEN")
    if dev_token:
        return dev_token

    access_token = get_access_token()
    if access_token is None:
        raise RuntimeError(
            "No authenticated request context: this tool must be called through an "
            "authorized MCP request."
        )
    return access_token.token


def create_server() -> FastMCP:
    """Build the configured FastMCP server."""
    mcp: FastMCP = FastMCP(SERVICE_NAME, auth=build_auth_provider())
    # Central record of every tool invocation. StructuredLoggingMiddleware's on_message
    # hook fires once per MCP request; `methods=["tools/call"]` narrows that to tool calls,
    # so a new tool module cannot forget to log the way a per-handler call could.
    # `include_payloads` logs the arguments — the bearer token is never one of them.
    mcp.add_middleware(
        StructuredLoggingMiddleware(logger=logger, include_payloads=True, methods=["tools/call"])
    )

    client = FreeAgentClient(
        token_provider=freeagent_token_provider,
        base_url=os.environ.get("FREEAGENT_API_BASE_URL", FREEAGENT_API_BASE_URL),
    )

    for module in TOOL_MODULES:
        module.register(mcp, client)

    @mcp.custom_route("/health", methods=["GET"])
    async def health_check(request: Request) -> JSONResponse:
        """Liveness probe. Custom routes bypass auth middleware by design, and this one
        deliberately touches neither FreeAgent nor OAuth state, so a cold container can
        answer it immediately."""
        return JSONResponse({"status": "healthy", "service": SERVICE_NAME})

    return mcp


def main() -> None:
    create_server().run(
        transport="http",
        # Binds all interfaces because the container runtime routes to it; not
        # directly exposed to the internet.
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        stateless_http=True,
    )


if __name__ == "__main__":
    main()
