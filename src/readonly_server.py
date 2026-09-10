"""Interim read-only MCP server.

A deliberately separate, deployable entrypoint from the fully-fledged connector in
`src/server.py`. It exposes a single generic read tool — `freeagent_get` — over any
FreeAgent path, GET only. This trades the connector's endpoint-per-tool guarantee (one
audited endpoint per tool, IDs validated by SafeId) for breadth now: a usable MCP over
the whole read surface of the API with almost no per-resource code.

The container's Dockerfile CMD selects which server is deployed; switching to the full
connector later is a one-line CMD change.
"""

from __future__ import annotations

import os
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.middleware.logging import StructuredLoggingMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.auth import FREEAGENT_API_BASE_URL, build_auth_provider
from src.client import FreeAgentClient
from src.log import logger
from src.server import freeagent_token_provider
from src.tools._helpers import freeagent_errors

READONLY_SERVICE_NAME = "freeagent-mcp-readonly"


def register(mcp: FastMCP, client: FreeAgentClient) -> None:
    """Register the single read-only tool.

    Mirrors the `src/tools/*` module pattern (a `register(mcp, client)` that injects the
    client) so the same test fixtures — `make_server`, `fa_client` — exercise it.
    """

    @mcp.tool
    async def freeagent_get(path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        """Read any FreeAgent API resource (GET only).

        Ensure you know the FreeAgent API documentation (paths and argument shape) before
        calling.

        Args:
            path: Relative API path, e.g. "/company", "/contacts", "/invoices/123".
            params: Optional query-string filters, e.g. {"view": "open", "per_page": "100"}.

        Note: by default only first 25 items are returned, you can change it to upto 100.
        This is a read-only window onto the FreeAgent account: it can list and fetch.
        Paths and query parameters mirror the
        FreeAgent REST API.

        Pagination: when a list has more pages than this call returned, the result carries
        a "pagination" object, e.g. {"next_page": 2, "total_count": 212}; its absence means
        the response is complete. Read the rest by calling again with {"page": "2"} (etc.)
        in `params`. Do not conclude a record is absent from a list without checking for
        "pagination" first — a page-sized result is often truncated, not the whole set.
        """
        with freeagent_errors():
            body, pagination = await client.get_paginated(path, params=params)
            if pagination and isinstance(body, dict):
                return {**body, "pagination": pagination}
            # Annotate the local so the Any-typed decoded body is narrowed to the declared
            # return type (get_paginated returns the body as Any, as JSON may be anything).
            result: dict[str, Any] = body
            return result


def create_readonly_server() -> FastMCP:
    """Build the interim read-only server: OAuth-gated, one generic read tool, /health.

    Reuses the full connector's OAuth (build_auth_provider) and token retrieval
    (freeagent_token_provider) unchanged — the only difference from create_server() is the
    tool surface: exactly one GET-only tool instead of the per-resource modules.
    """
    mcp: FastMCP = FastMCP(READONLY_SERVICE_NAME, auth=build_auth_provider())

    # Central tool-call logging. methods=["tools/call"] scopes it to tool invocations;
    # the exact string matters (a wrong value logs nothing). Mirrors src/server.py.
    mcp.add_middleware(
        StructuredLoggingMiddleware(logger=logger, include_payloads=True, methods=["tools/call"])
    )

    client = FreeAgentClient(
        token_provider=freeagent_token_provider,
        base_url=os.environ.get("FREEAGENT_API_BASE_URL", FREEAGENT_API_BASE_URL),
    )
    register(mcp, client)

    @mcp.custom_route("/health", methods=["GET"])
    async def health_check(request: Request) -> JSONResponse:
        """Liveness probe. Bypasses auth by design and touches neither FreeAgent nor OAuth
        state, so a cold container can answer it immediately."""
        return JSONResponse({"status": "healthy", "service": READONLY_SERVICE_NAME})

    return mcp


def main() -> None:
    create_readonly_server().run(
        transport="http",
        # Binds all interfaces because the container runtime routes to it; not directly
        # exposed to the internet.
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        stateless_http=True,
    )


if __name__ == "__main__":
    main()
