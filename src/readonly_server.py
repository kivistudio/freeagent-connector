"""Interim read-only MCP server.

A deliberately separate, deployable entrypoint from the fully-fledged connector in
`src/server.py`. It exposes a single generic read tool — `freeagent_get` — over any
FreeAgent path, GET only. This trades the connector's endpoint-per-tool guarantee (one
audited endpoint per tool, IDs validated by SafeId) for breadth now: a usable MCP over
the whole read surface of the API with almost no per-resource code.

Why that trade is acceptable *here* and not in `src/server.py`:
  - Read only. No tool can create, modify or delete accounting records — the write verbs
    are never exposed.
  - `FreeAgentClient`'s origin guard still applies to every call, so an injected path in
    FreeAgent data cannot send the bearer token off-origin.
  - Single-tenant. The only reader is the account owner, reading their own books, so the
    residual risk (an injected instruction steering a read to some *other* of the owner's
    own data) is low.

It is a SEPARATE entrypoint precisely so `src/server.py`'s `create_server()` and its
`TestApiCallerIsNotShipped` guarantee stay intact. The container's Dockerfile CMD selects
which server is deployed; switching to the full connector later is a one-line CMD change.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from src.client import FreeAgentClient
from src.tools._helpers import freeagent_errors


def register(mcp: FastMCP, client: FreeAgentClient) -> None:
    """Register the single read-only tool.

    Mirrors the `src/tools/*` module pattern (a `register(mcp, client)` that injects the
    client) so the same test fixtures — `make_server`, `fa_client` — exercise it.
    """

    @mcp.tool
    async def freeagent_get(path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        """Read any FreeAgent API resource (GET only).

        Args:
            path: Relative API path, e.g. "/company", "/contacts", "/invoices/123".
            params: Optional query-string filters, e.g. {"view": "open", "per_page": "50"}.

        This is a read-only window onto the FreeAgent account: it can list and fetch, but
        never create, change or delete anything. Useful paths include "/company",
        "/company/tax_timeline", "/contacts", "/invoices", "/bills", "/bank_accounts" and
        "/bank_transactions".
        """
        with freeagent_errors():
            result: dict[str, Any] = await client.get(path, params=params)
            return result
