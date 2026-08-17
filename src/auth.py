"""OAuth authorization for the MCP server.

FastMCP's `OAuthProxy` makes this server an OAuth authorization server toward MCP
clients while delegating the real grant to FreeAgent, so adding the connector is a
single browser consent flow. FreeAgent's tokens are opaque rather than JWTs, so the
verifier below follows the same shape as FastMCP's own GitHub provider: validate a
token by spending it on the cheapest authenticated endpoint.

Token storage and refresh belong to `OAuthProxy`. Nothing here persists a token.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

import httpx
from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.oauth_proxy import OAuthProxy

from src.utils import logger

FREEAGENT_API_BASE_URL = "https://api.freeagent.com/v2"
FREEAGENT_SANDBOX_API_BASE_URL = "https://api.sandbox.freeagent.com/v2"


@dataclass(frozen=True)
class UpstreamEndpoints:
    authorization: str
    token: str


def upstream_endpoints(api_base_url: str) -> UpstreamEndpoints:
    """Derive FreeAgent's OAuth endpoints from the API base URL.

    Derived rather than configured separately on purpose: it makes the
    production-OAuth-against-sandbox-API misconfiguration unrepresentable.
    """
    base = api_base_url.rstrip("/")
    return UpstreamEndpoints(
        authorization=f"{base}/approve_app",
        token=f"{base}/token_endpoint",
    )


class FreeAgentTokenVerifier(TokenVerifier):
    """Validates a FreeAgent access token by calling FreeAgent.

    FreeAgent issues opaque tokens, so there is no signature to check locally. `/users/me`
    is the cheapest authenticated endpoint and doubles as an identity lookup.
    """

    def __init__(
        self,
        api_base_url: str = FREEAGENT_API_BASE_URL,
        timeout_seconds: float = 10.0,
    ) -> None:
        super().__init__()
        self._api_base_url = api_base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.get(
                    f"{self._api_base_url}/users/me",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "User-Agent": "freeagent-mcp-remote",
                    },
                )
        except httpx.HTTPError as exc:
            # Fail closed. An unreachable FreeAgent means we cannot vouch for this token.
            logger.debug("FreeAgent token verification failed to reach the API: %s", exc)
            return None

        if response.status_code != 200:
            logger.debug("FreeAgent rejected a token with HTTP %s", response.status_code)
            return None

        user = response.json().get("user", {})
        subject = str(user.get("url", "unknown"))

        return AccessToken(
            token=token,  # the raw FreeAgent bearer, reachable via get_access_token().token
            client_id=subject,
            scopes=[],  # FreeAgent's OAuth has no scope system
            expires_at=None,  # expiry is OAuthProxy's business; it holds the refresh token
            subject=subject,
            claims={"sub": subject, "email": user.get("email")},
        )


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        # Names only — never the value, which for the secret would land in logs.
        raise RuntimeError(
            f"Missing required environment variable {name}. "
            "See .env.example for the full set of required settings."
        )
    return value


def build_auth_provider(
    require_authorization_consent: bool | Literal["remember", "external"] = True,
) -> OAuthProxy:
    """Construct the OAuthProxy for FreeAgent from environment configuration.

    `client_storage` and `jwt_signing_key` are deliberately left at their defaults: the
    encrypted local file store is wiped on every cold start, which is the accepted
    tradeoff recorded in the spec's storage decision, not an oversight.
    """
    client_id = _required_env("FREEAGENT_CLIENT_ID")
    client_secret = _required_env("FREEAGENT_CLIENT_SECRET")
    public_base_url = _required_env("PUBLIC_BASE_URL")
    api_base_url = os.environ.get("FREEAGENT_API_BASE_URL", FREEAGENT_API_BASE_URL)

    endpoints = upstream_endpoints(api_base_url)

    return OAuthProxy(
        upstream_authorization_endpoint=endpoints.authorization,
        upstream_token_endpoint=endpoints.token,
        upstream_client_id=client_id,
        upstream_client_secret=client_secret,
        token_verifier=FreeAgentTokenVerifier(api_base_url=api_base_url),
        base_url=public_base_url,
        require_authorization_consent=require_authorization_consent,
    )
