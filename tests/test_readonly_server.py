"""Tests for src/readonly_server.py — the interim read-only server.

These assert the two things that make this server safe to deploy despite its generic
path: it is GET-only (no way to steer it to a write verb), and every call still goes
through FreeAgentClient's origin guard.
"""

import contextlib
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
import respx
from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.exceptions import ToolError

from src.readonly_server import create_readonly_server, register
from tests.conftest import API


@pytest.fixture
def server(make_server: Callable[..., FastMCP]) -> FastMCP:
    return make_server(register)


class TestRegistration:
    async def test_exposes_exactly_the_read_tool(self, server: FastMCP) -> None:
        async with Client(transport=server) as client:
            names = {tool.name for tool in await client.list_tools()}
        assert names == {"freeagent_get"}

    async def test_the_read_tool_is_get_only(self, server: FastMCP) -> None:
        """A `path` argument is the deliberate interim tradeoff; a `method` argument would
        turn this into a write-capable generic tool, which it must never be."""
        async with Client(transport=server) as client:
            tools = await client.list_tools()
        tool = next(t for t in tools if t.name == "freeagent_get")
        props = (tool.input_schema or {}).get("properties", {})
        assert "path" in props
        assert "method" not in props

    async def test_the_tool_has_a_description(self, server: FastMCP) -> None:
        async with Client(transport=server) as client:
            tools = await client.list_tools()
        assert all(tool.description for tool in tools)


class TestFreeagentGet:
    @respx.mock
    async def test_calls_the_given_path_with_get(self, server: FastMCP) -> None:
        route = respx.get(f"{API}/company").mock(
            return_value=httpx.Response(200, json={"company": {"name": "Acme Ltd"}})
        )
        async with Client(transport=server) as client:
            await client.call_tool("freeagent_get", {"path": "/company"})
        assert route.called
        assert route.calls.last.request.method == "GET"

    @respx.mock
    async def test_returns_the_response_body(self, server: FastMCP) -> None:
        respx.get(f"{API}/company").mock(
            return_value=httpx.Response(200, json={"company": {"name": "Acme Ltd"}})
        )
        async with Client(transport=server) as client:
            result = await client.call_tool("freeagent_get", {"path": "/company"})
        assert result.data["company"]["name"] == "Acme Ltd"

    @respx.mock
    async def test_forwards_query_params(self, server: FastMCP) -> None:
        route = respx.get(f"{API}/contacts").mock(
            return_value=httpx.Response(200, json={"contacts": []})
        )
        async with Client(transport=server) as client:
            await client.call_tool(
                "freeagent_get", {"path": "/contacts", "params": {"view": "active"}}
            )
        assert route.calls.last.request.url.params["view"] == "active"

    @respx.mock
    async def test_adds_pagination_metadata_when_more_pages_exist(self, server: FastMCP) -> None:
        """The reported trap: a list truncated at a page boundary looked complete. When
        FreeAgent advertises a next page, the tool surfaces it so the caller fetches more."""
        next_link = '<https://api.freeagent.com/v2/invoices?page=2&per_page=25>; rel="next"'
        respx.get(f"{API}/invoices").mock(
            return_value=httpx.Response(
                200,
                json={"invoices": [{"id": 1}]},
                headers={"Link": next_link, "X-Total-Count": "212"},
            )
        )
        async with Client(transport=server) as client:
            result = await client.call_tool("freeagent_get", {"path": "/invoices"})
        assert result.data["invoices"] == [{"id": 1}]
        assert result.data["pagination"] == {"next_page": 2, "total_count": 212}

    @respx.mock
    async def test_omits_pagination_when_the_list_is_complete(self, server: FastMCP) -> None:
        """No next page means no signal to add — the body is returned unchanged, so single
        records and complete lists keep their exact FreeAgent shape."""
        respx.get(f"{API}/contacts").mock(return_value=httpx.Response(200, json={"contacts": []}))
        async with Client(transport=server) as client:
            result = await client.call_tool("freeagent_get", {"path": "/contacts"})
        assert "pagination" not in result.data


class TestSecurity:
    async def test_an_unsafe_path_is_blocked_by_the_origin_guard(self, server: FastMCP) -> None:
        """No respx mock: the guard must reject the path before any HTTP call is made, so
        the bearer token is never materialised for a hostile path."""
        async with Client(transport=server) as client:
            with pytest.raises(ToolError) as excinfo:
                await client.call_tool("freeagent_get", {"path": "../../evil"})
        assert "path safety guard" in str(excinfo.value)

    @respx.mock
    async def test_raises_tool_error_when_freeagent_fails(self, server: FastMCP) -> None:
        respx.get(f"{API}/company").mock(
            return_value=httpx.Response(403, json={"error": "forbidden", "message": "No access"})
        )
        async with Client(transport=server) as client:
            with pytest.raises(ToolError):
                await client.call_tool("freeagent_get", {"path": "/company"})


BASE = "https://mcp.example.com"


@contextlib.asynccontextmanager
async def asgi_client() -> AsyncIterator[httpx.AsyncClient]:
    """Drive the real Starlette app over ASGI, with its lifespan running, no sockets."""
    app = create_readonly_server().http_app(stateless_http=True)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            yield http


class TestReadonlyApp:
    async def test_health_responds_without_credentials(self, oauth_env: None) -> None:
        async with asgi_client() as http:
            response = await http.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy", "service": "freeagent-mcp-readonly"}

    async def test_mcp_requires_authentication(self, oauth_env: None) -> None:
        async with asgi_client() as http:
            response = await http.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Accept": "application/json, text/event-stream"},
            )
        assert response.status_code == 401

    async def test_the_built_server_exposes_only_the_read_tool(self, oauth_env: None) -> None:
        server = create_readonly_server()
        async with Client(transport=server) as client:
            names = {tool.name for tool in await client.list_tools()}
        assert names == {"freeagent_get"}
