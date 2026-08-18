"""Tests for src/server.py.

These exercise the real ASGI app rather than the in-memory transport, because the two
things worth proving here — that /health is reachable without credentials and that /mcp
is not — are properties of the HTTP middleware stack, not of any tool.

The app is entered via a context manager inside each test rather than an async fixture:
Starlette's lifespan uses an anyio cancel scope, which must be exited from the same task
that entered it, and pytest-asyncio's generator fixtures do not guarantee that.
"""

import contextlib
import logging
from collections.abc import AsyncGenerator

import httpx
import pytest
import respx
from fastmcp.client import Client

from src.server import create_server, freeagent_token_provider
from tests.conftest import API

BASE = "https://mcp.example.com"


@pytest.fixture
def oauth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FREEAGENT_CLIENT_ID", "cid")
    monkeypatch.setenv("FREEAGENT_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("PUBLIC_BASE_URL", BASE)
    monkeypatch.delenv("FREEAGENT_DEV_TOKEN", raising=False)


@contextlib.asynccontextmanager
async def asgi_client() -> AsyncGenerator[httpx.AsyncClient]:
    """Drive the real Starlette app over ASGI, with its lifespan running, no sockets."""
    app = create_server().http_app(stateless_http=True)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
            yield client


async def _post_mcp(client: httpx.AsyncClient, **headers: str) -> httpx.Response:
    return await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Accept": "application/json, text/event-stream", **headers},
    )


class TestHealthEndpoint:
    async def test_health_responds_without_any_credentials(self, oauth_env: None) -> None:
        """Scaleway's health probe has no token, and must not need one."""
        async with asgi_client() as http:
            response = await http.get("/health")
        assert response.status_code == 200

    async def test_health_reports_the_service_name(self, oauth_env: None) -> None:
        async with asgi_client() as http:
            response = await http.get("/health")
        assert response.json() == {"status": "healthy", "service": "freeagent-mcp-remote"}


class TestAuthGate:
    async def test_unauthenticated_mcp_request_is_rejected(self, oauth_env: None) -> None:
        async with asgi_client() as http:
            response = await _post_mcp(http)
        assert response.status_code == 401

    async def test_an_invalid_bearer_token_is_rejected(self, oauth_env: None) -> None:
        async with asgi_client() as http:
            response = await _post_mcp(http, Authorization="Bearer not-a-real-token")
        assert response.status_code == 401

    async def test_the_rejection_carries_a_resource_metadata_challenge(
        self, oauth_env: None
    ) -> None:
        """A bare 401 is not enough. Claude's 'Add connector' flow auto-detects OAuth by
        following WWW-Authenticate to the protected-resource metadata, so the header must
        be present and must name that document."""
        async with asgi_client() as http:
            response = await _post_mcp(http)
        challenge = response.headers.get("WWW-Authenticate", "")
        assert challenge.startswith("Bearer ")
        assert "resource_metadata=" in challenge

    async def test_the_advertised_metadata_document_actually_exists(self, oauth_env: None) -> None:
        """Follows the challenge rather than hardcoding the path, so this keeps passing
        if FastMCP changes where the document lives — and still fails if the pointer and
        the document ever disagree, which is the bug worth catching."""
        async with asgi_client() as http:
            challenge = (await _post_mcp(http)).headers["WWW-Authenticate"]
            url = challenge.split('resource_metadata="', 1)[1].split('"', 1)[0]
            response = await http.get(url)
        assert response.status_code == 200
        assert "authorization_servers" in response.json()

    async def test_oauth_authorization_server_metadata_is_served(self, oauth_env: None) -> None:
        async with asgi_client() as http:
            response = await http.get("/.well-known/oauth-authorization-server")
        assert response.status_code == 200


class TestTokenProvider:
    def test_dev_token_override_is_off_unless_explicitly_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The local-development escape hatch must never be the default path. With no
        FREEAGENT_DEV_TOKEN set and no OAuth request context, this must fail rather than
        quietly fall back to something."""
        monkeypatch.delenv("FREEAGENT_DEV_TOKEN", raising=False)
        with pytest.raises(Exception):  # noqa: B017 - any failure is fine; silence is not
            freeagent_token_provider()

    def test_dev_token_is_used_when_explicitly_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FREEAGENT_DEV_TOKEN", "local-dev-token")
        assert freeagent_token_provider() == "local-dev-token"


class TestToolRegistration:
    async def test_company_tools_are_registered(self, oauth_env: None) -> None:
        server = create_server()
        async with Client(transport=server) as client:
            names = {tool.name for tool in await client.list_tools()}
        assert {"freeagent_get_company", "freeagent_get_tax_timeline"} <= names


class TestToolCallLogging:
    """Tool invocations are logged centrally by StructuredLoggingMiddleware.

    This replaces the old per-handler `log_tool_call`: the server wires one middleware
    that logs every `tools/call`, so a new tool module cannot forget to log. The marker
    proving the middleware (not some leftover per-handler call) produced the record is the
    `tools/call` method string, which the old mechanism never emitted.
    """

    @respx.mock
    async def test_tool_calls_are_logged_with_the_tool_name(
        self, oauth_env: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("FREEAGENT_DEV_TOKEN", "local-dev-token")
        respx.get(f"{API}/company").mock(
            return_value=httpx.Response(200, json={"company": {"name": "Acme Ltd"}})
        )
        server = create_server()
        with caplog.at_level(logging.INFO, logger="freeagent_mcp"):
            async with Client(transport=server) as client:
                await client.call_tool("freeagent_get_company", {})
        assert "tools/call" in caplog.text
        assert "freeagent_get_company" in caplog.text


class TestApiCallerIsNotShipped:
    """The generic request tool in scripts/freeagent_api_caller.py must never reach production.

    Every production tool exposes exactly one endpoint with its IDs validated by SafeId.
    A generic 'call any endpoint' tool would hand arbitrary API access to a model reading
    FreeAgent data that may carry injected instructions, undoing that design. This test
    is the enforcement; do not weaken it.
    """

    async def test_production_server_exposes_no_generic_request_tool(self, oauth_env: None) -> None:
        from scripts.freeagent_api_caller import GENERIC_REQUEST_TOOL

        server = create_server()
        async with Client(transport=server) as client:
            names = {tool.name for tool in await client.list_tools()}
        assert GENERIC_REQUEST_TOOL not in names

    async def test_every_production_tool_targets_one_endpoint(self, oauth_env: None) -> None:
        """A production tool taking a free-form `path` or `method` argument would be the
        same hole by another name."""
        server = create_server()
        async with Client(transport=server) as client:
            tools = await client.list_tools()
        for tool in tools:
            properties = (tool.input_schema or {}).get("properties", {})
            assert "path" not in properties, f"{tool.name} accepts a free-form path"
            assert "method" not in properties, f"{tool.name} accepts a free-form method"
