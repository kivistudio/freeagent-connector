"""Tests for src/auth.py.

This layer leans on FastMCP's own OAuth machinery, so these tests verify OUR wiring —
that the right upstream endpoints are configured, that a FreeAgent token is validated
against FreeAgent, and that config errors surface clearly — not FastMCP's internals.
"""

import httpx
import pytest
import respx

from src.auth import (
    FREEAGENT_SANDBOX_API_BASE_URL,
    FreeAgentTokenVerifier,
    build_auth_provider,
    upstream_endpoints,
)


class TestUpstreamEndpoints:
    def test_derives_production_oauth_endpoints_from_the_api_base(self) -> None:
        endpoints = upstream_endpoints("https://api.freeagent.com/v2")
        assert endpoints.authorization == "https://api.freeagent.com/v2/approve_app"
        assert endpoints.token == "https://api.freeagent.com/v2/token_endpoint"

    def test_derives_sandbox_oauth_endpoints_from_the_sandbox_api_base(self) -> None:
        """Deriving rather than configuring separately makes it impossible to point the
        OAuth flow at production while the API client talks to sandbox."""
        endpoints = upstream_endpoints(FREEAGENT_SANDBOX_API_BASE_URL)
        assert endpoints.authorization == "https://api.sandbox.freeagent.com/v2/approve_app"
        assert endpoints.token == "https://api.sandbox.freeagent.com/v2/token_endpoint"

    def test_tolerates_a_trailing_slash_on_the_api_base(self) -> None:
        endpoints = upstream_endpoints("https://api.freeagent.com/v2/")
        assert endpoints.authorization == "https://api.freeagent.com/v2/approve_app"


class TestFreeAgentTokenVerifier:
    @respx.mock
    async def test_accepts_a_token_freeagent_recognises(self) -> None:
        respx.get("https://api.freeagent.com/v2/users/me").mock(
            return_value=httpx.Response(
                200,
                json={"user": {"url": "https://api.freeagent.com/v2/users/42", "email": "a@b.com"}},
            )
        )
        verifier = FreeAgentTokenVerifier()
        result = await verifier.verify_token("good-token")
        assert result is not None

    @respx.mock
    async def test_the_verified_token_carries_the_raw_freeagent_bearer(self) -> None:
        """Tool handlers reach the FreeAgent token via get_access_token().token, so the
        raw upstream string must survive verification unchanged."""
        respx.get("https://api.freeagent.com/v2/users/me").mock(
            return_value=httpx.Response(200, json={"user": {"url": ".../users/42"}})
        )
        verifier = FreeAgentTokenVerifier()
        result = await verifier.verify_token("raw-upstream-token")
        assert result is not None
        assert result.token == "raw-upstream-token"

    @respx.mock
    async def test_sends_the_token_as_a_bearer_credential(self) -> None:
        route = respx.get("https://api.freeagent.com/v2/users/me").mock(
            return_value=httpx.Response(200, json={"user": {}})
        )
        await FreeAgentTokenVerifier().verify_token("tok")
        assert route.calls.last.request.headers["Authorization"] == "Bearer tok"

    @respx.mock
    async def test_rejects_a_token_freeagent_refuses(self) -> None:
        respx.get("https://api.freeagent.com/v2/users/me").mock(
            return_value=httpx.Response(401, json={"error": "unauthorized"})
        )
        assert await FreeAgentTokenVerifier().verify_token("bad-token") is None

    @respx.mock
    async def test_rejects_when_freeagent_is_unreachable(self) -> None:
        """A network failure must fail closed, not open."""
        respx.get("https://api.freeagent.com/v2/users/me").mock(
            side_effect=httpx.ConnectError("boom")
        )
        assert await FreeAgentTokenVerifier().verify_token("tok") is None

    @respx.mock
    async def test_verifies_against_sandbox_when_configured_for_sandbox(self) -> None:
        route = respx.get(f"{FREEAGENT_SANDBOX_API_BASE_URL}/users/me").mock(
            return_value=httpx.Response(200, json={"user": {}})
        )
        verifier = FreeAgentTokenVerifier(api_base_url=FREEAGENT_SANDBOX_API_BASE_URL)
        await verifier.verify_token("tok")
        assert route.called


class TestBuildAuthProvider:
    def test_builds_a_provider_from_environment_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FREEAGENT_CLIENT_ID", "cid")
        monkeypatch.setenv("FREEAGENT_CLIENT_SECRET", "csecret")
        monkeypatch.setenv("PUBLIC_BASE_URL", "https://mcp.example.com")
        provider = build_auth_provider()
        assert provider is not None

    @pytest.mark.parametrize(
        "missing", ["FREEAGENT_CLIENT_ID", "FREEAGENT_CLIENT_SECRET", "PUBLIC_BASE_URL"]
    )
    def test_reports_which_setting_is_missing(
        self, monkeypatch: pytest.MonkeyPatch, missing: str
    ) -> None:
        monkeypatch.setenv("FREEAGENT_CLIENT_ID", "cid")
        monkeypatch.setenv("FREEAGENT_CLIENT_SECRET", "csecret")
        monkeypatch.setenv("PUBLIC_BASE_URL", "https://mcp.example.com")
        monkeypatch.delenv(missing, raising=False)
        with pytest.raises(RuntimeError) as excinfo:
            build_auth_provider()
        assert missing in str(excinfo.value)

    def test_the_error_does_not_echo_the_client_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FREEAGENT_CLIENT_SECRET", "super-secret-value")
        monkeypatch.delenv("FREEAGENT_CLIENT_ID", raising=False)
        monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
        with pytest.raises(RuntimeError) as excinfo:
            build_auth_provider()
        assert "super-secret-value" not in str(excinfo.value)
