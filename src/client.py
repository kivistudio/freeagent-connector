"""Thin async HTTP wrapper around the FreeAgent API.

Re-expresses the request-wrapper logic from samaxbytez/freeagent-mcp's `client.ts` in
Python. The path-safety guard below is the security boundary of this
project and must never be weakened; see `docs/plans/freeagent-mcp-remote.md`.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

DEFAULT_BASE_URL = "https://api.freeagent.com/v2"
USER_AGENT = "freeagent-mcp-remote"

# Check 1: `..` followed by a path separator. Deliberately NOT a bare `..` substring —
# `..foo` is a legitimate (if odd) path segment and matching it would be over-broad.
_TRAVERSAL_PATTERN = re.compile(r"\.\.[\\/]")

# Check 2: an absolute URL scheme prefix, case-insensitive (https://, ftp://, javascript://).
_SCHEME_PATTERN = re.compile(r"^[a-z]+://", re.IGNORECASE)

TokenProvider = Callable[[], str | Awaitable[str]]


class UnsafePathError(ValueError):
    """A request path failed the safety guard and was never sent."""


class FreeAgentApiError(Exception):
    """FreeAgent returned a non-2xx response."""

    def __init__(self, status: int, error_code: str, message: str) -> None:
        super().__init__(f"FreeAgent API error ({status}): {error_code} - {message}")
        self.status = status
        self.error_code = error_code


def _parse_api_error(status: int, body: str) -> FreeAgentApiError:
    """Turn an error response into a structured error.

    A body we cannot parse is discarded rather than echoed. It is upstream content headed
    for an LLM's context, and splicing an arbitrary HTML error page into a tool error is
    both an injection surface and a way to leak internals.
    """
    try:
        parsed = json.loads(body)
    except ValueError, TypeError:
        return FreeAgentApiError(status, "unknown", f"HTTP {status} error")

    if not isinstance(parsed, dict):
        return FreeAgentApiError(status, "unknown", f"HTTP {status} error")

    code = parsed.get("error") or parsed.get("code") or "unknown"
    nested = parsed.get("errors")
    nested_message = None
    if isinstance(nested, dict):
        inner = nested.get("error")
        if isinstance(inner, dict):
            nested_message = inner.get("message")

    message = (
        parsed.get("message")
        or parsed.get("error_description")
        or nested_message
        or f"HTTP {status}"
    )
    return FreeAgentApiError(status, str(code), str(message))


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


class FreeAgentClient:
    """Issues authenticated requests to FreeAgent, refusing any path that leaves it."""

    def __init__(
        self,
        token_provider: TokenProvider,
        base_url: str = DEFAULT_BASE_URL,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._base_url = base_url.rstrip("/")
        self._http_client = http_client

    def _resolve_url(self, path: str) -> str:
        """Resolve a relative API path, rejecting anything that could leave our origin.

        Three independent gates, in this order. Order is load-bearing: checks 1 and 2 run
        on the raw string so obviously-hostile input is refused before any URL parsing,
        and check 3 is the backstop that catches whatever slips past them.

        Raises:
            UnsafePathError: on any of the three checks failing.
        """
        base = self._base_url + "/"
        full_path = path[1:] if path.startswith("/") else path

        if _TRAVERSAL_PATTERN.search(full_path) or _SCHEME_PATTERN.match(full_path):
            raise UnsafePathError(f"Unsafe API path rejected: {full_path!r}")

        url = urljoin(base, full_path)

        if _origin(url) != _origin(base):
            raise UnsafePathError(
                f"Resolved URL origin does not match the FreeAgent API base: {_origin(url)!r}"
            )

        return url

    async def _token(self) -> str:
        """Fetch the bearer token for this request.

        Called per-request and never cached: OAuthProxy refreshes the upstream token
        underneath us, so a cached copy would eventually be a stale one.
        """
        result = self._token_provider()
        if inspect.isawaitable(result):
            return await result
        return result

    async def request_raw(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Make a guarded request and return the raw response.

        Exists for callers that need response *headers*: the command-line tool in
        `scripts/freeagent_api_caller.py` reads `X-Total-Count` and `Link` to pin down
        FreeAgent's undocumented pagination behaviour. Everything else should call
        `request`, which returns the decoded body.
        """
        # Guard first, before the token is even read — a rejected path must never cause
        # the bearer token to be materialised, let alone sent.
        url = self._resolve_url(path)

        headers = {
            "Authorization": f"Bearer {await self._token()}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }

        client = self._http_client or httpx.AsyncClient(timeout=30.0)
        owns_client = self._http_client is None
        try:
            response = await client.request(
                method, url, headers=headers, params=params, json=json_body
            )
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code >= 400:
            raise _parse_api_error(response.status_code, response.text)

        return response

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """Make a guarded request and return the decoded JSON body."""
        response = await self.request_raw(method, path, params=params, json_body=json_body)
        if not response.text:
            return {}
        return response.json()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self.request("GET", path, params=params)

    async def post(
        self,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return await self.request("POST", path, params=params, json_body=json)

    async def put(
        self,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return await self.request("PUT", path, params=params, json_body=json)

    async def delete(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self.request("DELETE", path, params=params)
