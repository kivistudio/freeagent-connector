"""Company profile and tax timeline.

This module establishes the registration pattern every other tool module follows:

    def register(mcp: FastMCP, client: FreeAgentClient) -> None:
        @mcp.tool
        async def freeagent_something(...) -> dict:
            with freeagent_errors():
                return await client.get("/relative/path")

Every tool call is logged centrally by StructuredLoggingMiddleware (wired in
`src/server.py`), so handlers do not log themselves.

The client is passed in rather than imported, so tests can supply one whose HTTP layer is
mocked, and so the token provider stays a server-level concern.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from src.client import FreeAgentClient
from src.tools._helpers import freeagent_errors


def register(mcp: FastMCP, client: FreeAgentClient) -> None:
    @mcp.tool
    async def freeagent_get_company() -> dict[str, Any]:
        """Get this company's profile and accounting configuration.

        Call this first when answering any year-end, VAT or tax question: it carries the
        accounting year-end dates, the company type, and the VAT registration status and
        basis that frame every other figure. Returns fields including `name`,
        `company_start_date`, `first_accounting_year_end`, `annual_accounting_periods`,
        `sales_tax_registration_status` and `initial_vat_basis`.

        Note that the VAT fields describe the position at registration. If the company has
        since changed scheme they may be out of date, so do not present them as the
        current scheme without corroboration.
        """
        with freeagent_errors():
            result: dict[str, Any] = await client.get("/company")
            return result

    @mcp.tool
    async def freeagent_get_tax_timeline() -> dict[str, Any]:
        """List upcoming tax obligations with their due dates and amounts.

        Each item carries a `description`, `dated_on` due date, `amount_due`, and an
        `is_personal` flag separating the director's obligations from the company's.
        """
        with freeagent_errors():
            result: dict[str, Any] = await client.get("/company/tax_timeline")
            return result
