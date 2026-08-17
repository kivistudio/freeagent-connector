"""Tests for src/tools/company.py — the module that establishes the tool pattern.

Every other tool module follows the shape verified here, so these assertions are about
the pattern as much as about company data: correct registration, exact HTTP call, plain
return values, and ToolError (not a returned error value) on failure.
"""

from collections.abc import Callable

import httpx
import pytest
import respx
from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.exceptions import ToolError

from src.tools.company import register
from tests.conftest import API


@pytest.fixture
def server(make_server: Callable[..., FastMCP]) -> FastMCP:
    return make_server(register)


class TestRegistration:
    async def test_registers_the_expected_tools(self, server: FastMCP) -> None:
        async with Client(transport=server) as client:
            names = {tool.name for tool in await client.list_tools()}
        assert names == {"freeagent_get_company", "freeagent_get_tax_timeline"}

    async def test_every_tool_has_a_description(self, server: FastMCP) -> None:
        """Descriptions are how the model decides which tool to call; a blank one is a bug."""
        async with Client(transport=server) as client:
            tools = await client.list_tools()
        assert all(tool.description for tool in tools)


class TestGetCompany:
    @respx.mock
    async def test_calls_the_company_endpoint(self, server: FastMCP) -> None:
        route = respx.get(f"{API}/company").mock(
            return_value=httpx.Response(200, json={"company": {"name": "Acme Ltd"}})
        )
        async with Client(transport=server) as client:
            await client.call_tool("freeagent_get_company", {})
        assert route.called
        assert route.calls.last.request.method == "GET"

    @respx.mock
    async def test_returns_the_company_payload(self, server: FastMCP) -> None:
        respx.get(f"{API}/company").mock(
            return_value=httpx.Response(
                200, json={"company": {"name": "Acme Ltd", "company_start_date": "2019-04-01"}}
            )
        )
        async with Client(transport=server) as client:
            result = await client.call_tool("freeagent_get_company", {})
        assert result.data["company"]["name"] == "Acme Ltd"


class TestGetTaxTimeline:
    @respx.mock
    async def test_calls_the_tax_timeline_endpoint(self, server: FastMCP) -> None:
        route = respx.get(f"{API}/company/tax_timeline").mock(
            return_value=httpx.Response(200, json={"timeline_items": []})
        )
        async with Client(transport=server) as client:
            await client.call_tool("freeagent_get_tax_timeline", {})
        assert route.called

    @respx.mock
    async def test_returns_timeline_items(self, server: FastMCP) -> None:
        respx.get(f"{API}/company/tax_timeline").mock(
            return_value=httpx.Response(
                200,
                json={
                    "timeline_items": [
                        {
                            "description": "Corporation Tax",
                            "dated_on": "2027-01-01",
                            "amount_due": "4200.00",
                            "is_personal": False,
                        }
                    ]
                },
            )
        )
        async with Client(transport=server) as client:
            result = await client.call_tool("freeagent_get_tax_timeline", {})
        assert result.data["timeline_items"][0]["description"] == "Corporation Tax"


class TestErrorHandling:
    @respx.mock
    async def test_raises_tool_error_when_freeagent_fails(self, server: FastMCP) -> None:
        """Failures must raise ToolError, never return an error-shaped value — ToolError's
        message reaches the client even when error masking is on."""
        respx.get(f"{API}/company").mock(
            return_value=httpx.Response(403, json={"error": "forbidden", "message": "No access"})
        )
        async with Client(transport=server) as client:
            with pytest.raises(ToolError):
                await client.call_tool("freeagent_get_company", {})

    @respx.mock
    async def test_the_error_message_names_the_failure(self, server: FastMCP) -> None:
        respx.get(f"{API}/company").mock(
            return_value=httpx.Response(403, json={"error": "forbidden", "message": "No access"})
        )
        async with Client(transport=server) as client:
            with pytest.raises(ToolError) as excinfo:
                await client.call_tool("freeagent_get_company", {})
        assert "403" in str(excinfo.value)

    @respx.mock
    async def test_the_error_message_does_not_leak_the_bearer_token(self, server: FastMCP) -> None:
        respx.get(f"{API}/company").mock(
            return_value=httpx.Response(401, json={"error": "unauthorized"})
        )
        async with Client(transport=server) as client:
            with pytest.raises(ToolError) as excinfo:
                await client.call_tool("freeagent_get_company", {})
        assert "test-token" not in str(excinfo.value)
