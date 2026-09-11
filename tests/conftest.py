"""Shared fixtures for every tool-module test suite."""

from collections.abc import Callable

import pytest
from fastmcp import FastMCP

from src.client import DEFAULT_BASE_URL, FreeAgentClient

# Re-exported so tool tests can build mock URLs without importing from src.client directly.
API = DEFAULT_BASE_URL


@pytest.fixture
def fa_client() -> FreeAgentClient:
    """A FreeAgentClient with a static token, for use with respx-mocked HTTP."""
    return FreeAgentClient(token_provider=lambda: "test-token")


@pytest.fixture
def make_server(fa_client: FreeAgentClient) -> Callable[..., FastMCP]:
    """Build an unauthenticated FastMCP server with the given tool modules registered.

    Auth is deliberately absent here: these suites test tool behaviour, and the auth gate
    is covered separately in test_server.py against the real ASGI app.
    """

    def _make(*register_fns: Callable[[FastMCP, FreeAgentClient], None]) -> FastMCP:
        mcp: FastMCP = FastMCP("test-server")
        for register in register_fns:
            register(mcp, fa_client)
        return mcp

    return _make


@pytest.fixture
def oauth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment build_auth_provider() requires, for tests that build a real server."""
    monkeypatch.setenv("FREEAGENT_CLIENT_ID", "cid")
    monkeypatch.setenv("FREEAGENT_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://mcp.example.com")
    monkeypatch.delenv("FREEAGENT_DEV_TOKEN", raising=False)
