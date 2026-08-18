# Interim Read-Only MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a deployable, read-only FreeAgent MCP server that exposes a single generic `freeagent_get` tool over any API path, as a deliberately separate entrypoint from the not-yet-finished full connector.

**Architecture:** A new `src/readonly_server.py` module reuses the existing OAuth (`build_auth_provider`), token retrieval (`freeagent_token_provider`), origin-guarded HTTP client (`FreeAgentClient`) and `/health` route, but registers exactly one tool: `freeagent_get(path, params)`, which is GET-only. It is a *separate* entrypoint from `src/server.py`'s `create_server()`, so the full connector's endpoint-per-tool guarantee and its `TestApiCallerIsNotShipped` test stay untouched. The deployed container's Dockerfile `CMD` selects this server now; switching to the full connector later is a one-line `CMD` change.

**Tech Stack:** Python 3.14, FastMCP v4 (`fastmcp==4.0.0b3`), httpx, Starlette, pytest + pytest-asyncio + respx, uv, ruff, mypy (strict), Docker.

**Spec:** `docs/plans/freeagent-mcp-remote.md` (the full connector tech spec). This plan implements a *new interim deliverable* alongside it, labelled "(b)" in the design discussion, decoupled from the connector "(c)".

## Global Constraints

Copied verbatim from the spec and repo config. Every task's requirements implicitly include these.

- **Python floor:** `requires-python = ">=3.14"`; ruff `target-version = "py314"`, `line-length = 100`.
- **Pinned deps:** `fastmcp==4.0.0b3`, `httpx>=0.28,<1.0.dev0`, `pydantic>=2.11,<2.14.0a0`, `uvicorn>=0.35,<1.0.dev0`. Do not add dependencies.
- **mypy strict** over `src`, `tests`, `scripts` — every new function is fully typed.
- **Tests:** `pytest` with `asyncio_mode = "auto"`, `respx` for httpx mocking, FastMCP's in-memory `Client` for tool invocation. One `respx.get(...)` mock per external call.
- **Origin-check guard in `src/client.py` must never be weakened.** The read-only server relies on it: every `freeagent_get` call routes through `FreeAgentClient`, so its path-traversal / off-origin rejection still protects the bearer token.
- **`create_server()` in `src/server.py` and the assertions of `tests/test_server.py::TestApiCallerIsNotShipped` are out of scope and must not change.** The generic read tool lives only on the *separate* read-only server. The single permitted edit to `tests/test_server.py` is relocating its shared `oauth_env` fixture to `conftest.py` (Task 2), which changes no test's behaviour.
- **`scripts/` is never deployed.** The read-only server must not import from `scripts/`, and `.dockerignore` must exclude `scripts/`.
- **Never log the bearer token.** Tool-call logging is handled centrally by
  `StructuredLoggingMiddleware` (registered in `create_readonly_server()`, mirroring
  `src/server.py`), not a per-tool call. The bearer token is never a tool parameter, so it
  is never in a logged payload.
- **Commits:** Conventional Commits (`feat:`, `docs:`, etc.).

---

## File Structure

- **Create `src/readonly_server.py`** — the interim server. Exports `register(mcp, client)` (adds the one `freeagent_get` tool, mirroring the `src/tools/*` module pattern so existing test fixtures exercise it), `create_readonly_server()` (assembles the OAuth-gated FastMCP app + `/health`), and `main()` (the container entrypoint). Reuses `build_auth_provider`, `FREEAGENT_API_BASE_URL` (from `src/auth.py`), `freeagent_token_provider` (from `src/server.py`), `FreeAgentClient` (`src/client.py`), `freeagent_errors` (`src/tools/_helpers.py`), and `logger` (`src/log.py`, for the logging middleware).
- **Create `tests/test_readonly_server.py`** — behaviour of `freeagent_get`, the "exactly one tool / GET-only" shape guarantees, the origin-guard-still-applies check, and one health/auth check against the real ASGI app.
- **Modify `tests/conftest.py`** — add a shared `oauth_env` fixture (the read-only server's ASGI test needs OAuth env vars; putting it in conftest lets both test files share one definition).
- **Modify `tests/test_server.py`** — remove its now-redundant local `oauth_env` fixture so there is a single shared copy in conftest. This is the *only* edit to this file; `TestApiCallerIsNotShipped` and every other test's assertions are unchanged, and they keep passing because conftest's `oauth_env` sets the identical env vars.
- **Create `Dockerfile`** — the multi-stage uv build from the spec, with its `CMD` running `src.readonly_server`.
- **Create `.dockerignore`** — excludes `.env`, `scripts/`, `.venv/`, `docs/`, `tests/`, caches, `.git`.
- **Modify `docs/plans/freeagent-mcp-remote.md`** — record the interim read-only server as a deliberate, separate deliverable with its weaker-but-bounded security posture, and add task-list entries.
- **Modify `docs/plans/freeagent-mcp-remote-deployment.md`** — note which server is deployed now and how the `CMD` switches to the full connector later.
- **Modify `CLAUDE.md`** — a short note so a future reader/agent understands the free-form-path tool on the read-only entrypoint is intentional, not a violation of the connector's invariant.

---

## Task 1: The read-only `freeagent_get` tool

Delivers `register(mcp, client)` adding one GET-only tool over any FreeAgent path, tested with the existing `make_server` / `fa_client` fixtures and respx. No OAuth env needed for this task's tests.

**Files:**
- Create: `src/readonly_server.py` (the `register` function and the `freeagent_get` tool only; the rest of the module is added in Task 2)
- Test: `tests/test_readonly_server.py`

**Interfaces:**
- Consumes: `make_server(*register_fns) -> FastMCP` and `fa_client` (from `tests/conftest.py`); `API` (base URL string, from `tests/conftest.py`); `freeagent_errors` (`src/tools/_helpers.py`); `FreeAgentClient.get(path, params=None) -> Any` (`src/client.py`). Tool-call logging is not a concern of this tool — it is added centrally as middleware in Task 2.
- Produces: `register(mcp: FastMCP, client: FreeAgentClient) -> None`, which registers the tool `freeagent_get(path: str, params: dict[str, str] | None = None) -> dict[str, Any]`. Task 2 calls `register`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_readonly_server.py`:

```python
"""Tests for src/readonly_server.py — the interim read-only server.

These assert the two things that make this server safe to deploy despite its generic
path: it is GET-only (no way to steer it to a write verb), and every call still goes
through FreeAgentClient's origin guard.
"""

from collections.abc import Callable

import httpx
import pytest
import respx
from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.exceptions import ToolError

from src.readonly_server import register
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_readonly_server.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'src.readonly_server'` (the module does not exist yet).

- [ ] **Step 3: Write the minimal module with the tool**

Create `src/readonly_server.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_readonly_server.py -v`
Expected: PASS (all tests in `TestRegistration`, `TestFreeagentGet`, `TestSecurity`).

- [ ] **Step 5: Lint and type-check the new files**

Run: `uv run ruff check src/readonly_server.py tests/test_readonly_server.py && uv run mypy src/readonly_server.py tests/test_readonly_server.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/readonly_server.py tests/test_readonly_server.py
git commit -m "feat: add read-only freeagent_get tool for the interim server"
```

---

## Task 2: The read-only server entrypoint

Assembles the OAuth-gated FastMCP app, the `/health` route, and the `main()` container entrypoint around the Task 1 tool, and verifies the real ASGI app.

**Files:**
- Modify: `src/readonly_server.py` (add imports, `READONLY_SERVICE_NAME`, `create_readonly_server`, `main`, and the `__main__` guard)
- Modify: `tests/conftest.py` (add a shared `oauth_env` fixture)
- Modify: `tests/test_server.py` (remove its now-redundant local `oauth_env` fixture)
- Test: `tests/test_readonly_server.py` (add `TestReadonlyApp`)

**Interfaces:**
- Consumes: `register` (from Task 1); `build_auth_provider() -> OAuthProxy` and `FREEAGENT_API_BASE_URL` (`src/auth.py`); `freeagent_token_provider() -> str` (`src/server.py`); `FreeAgentClient` (`src/client.py`); `logger` (`src/log.py`) and `StructuredLoggingMiddleware` (`fastmcp.server.middleware.logging`) for tool-call logging.
- Produces: `READONLY_SERVICE_NAME = "freeagent-mcp-readonly"`; `create_readonly_server() -> FastMCP`; `main() -> None`. The Dockerfile in Task 3 invokes `python -m src.readonly_server`, which runs `main()`.

- [ ] **Step 1: Add the shared `oauth_env` fixture to conftest**

In `tests/conftest.py`, add this fixture (append after the existing fixtures). `build_auth_provider` reads these env vars at construction time, and clearing `FREEAGENT_DEV_TOKEN` keeps the dev-token escape hatch out of these tests:

```python
@pytest.fixture
def oauth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment build_auth_provider() requires, for tests that build a real server."""
    monkeypatch.setenv("FREEAGENT_CLIENT_ID", "cid")
    monkeypatch.setenv("FREEAGENT_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://mcp.example.com")
    monkeypatch.delenv("FREEAGENT_DEV_TOKEN", raising=False)
```

`tests/conftest.py` already imports `pytest`, so no new import is needed.

Then, in `tests/test_server.py`, delete its now-redundant local copy (the tests that use it pick up the identical conftest one automatically). Remove exactly this block:

```python
@pytest.fixture
def oauth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FREEAGENT_CLIENT_ID", "cid")
    monkeypatch.setenv("FREEAGENT_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("PUBLIC_BASE_URL", BASE)
    monkeypatch.delenv("FREEAGENT_DEV_TOKEN", raising=False)
```

Leave `test_server.py`'s `BASE = "https://mcp.example.com"` constant in place — it is still used by `asgi_client()`, and it is the value conftest's `oauth_env` sets `PUBLIC_BASE_URL` to, so the two agree.

- [ ] **Step 2: Write the failing app-level tests**

Add to `tests/test_readonly_server.py` (add the new imports at the top of the file, then the class):

```python
import contextlib
from collections.abc import AsyncIterator

from src.readonly_server import create_readonly_server

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
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_readonly_server.py::TestReadonlyApp -v`
Expected: FAIL with `ImportError: cannot import name 'create_readonly_server' from 'src.readonly_server'`.

- [ ] **Step 4: Implement the server assembly and entrypoint**

Edit `src/readonly_server.py`. Add these imports to the existing import block:

```python
import os

from fastmcp.server.middleware.logging import StructuredLoggingMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.auth import FREEAGENT_API_BASE_URL, build_auth_provider
from src.log import logger
from src.server import freeagent_token_provider
```

Add the constant near the top of the module (after the imports):

```python
READONLY_SERVICE_NAME = "freeagent-mcp-readonly"
```

Append after the `register` function:

```python
def create_readonly_server() -> FastMCP:
    """Build the interim read-only server: OAuth-gated, one generic read tool, /health.

    Reuses the full connector's OAuth (build_auth_provider) and token retrieval
    (freeagent_token_provider) unchanged — the only difference from create_server() is the
    tool surface: exactly one GET-only tool instead of the per-resource modules.
    """
    mcp: FastMCP = FastMCP(READONLY_SERVICE_NAME, auth=build_auth_provider())

    # Central tool-call logging. methods=["tools/call"] scopes it to tool invocations;
    # the exact string matters (a wrong value logs nothing). Mirrors src/server.py.
    mcp.add_middleware(
        StructuredLoggingMiddleware(logger=logger, include_payloads=True, methods=["tools/call"])
    )

    client = FreeAgentClient(
        token_provider=freeagent_token_provider,
        base_url=os.environ.get("FREEAGENT_API_BASE_URL", FREEAGENT_API_BASE_URL),
    )
    register(mcp, client)

    @mcp.custom_route("/health", methods=["GET"])
    async def health_check(request: Request) -> JSONResponse:
        """Liveness probe. Bypasses auth by design and touches neither FreeAgent nor OAuth
        state, so a cold container can answer it immediately."""
        return JSONResponse({"status": "healthy", "service": READONLY_SERVICE_NAME})

    return mcp


def main() -> None:
    create_readonly_server().run(
        transport="http",
        # Binds all interfaces because the container runtime routes to it; not directly
        # exposed to the internet.
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        stateless_http=True,
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_readonly_server.py::TestReadonlyApp -v`
Expected: PASS.

- [ ] **Step 6: Run the whole suite, lint, and type-check**

Run: `uv run pytest && uv run ruff check . && uv run mypy`
Expected: all tests pass (including the untouched `tests/test_server.py`), no lint or mypy errors.

- [ ] **Step 7: Commit**

```bash
git add src/readonly_server.py tests/test_readonly_server.py tests/conftest.py
git commit -m "feat: add create_readonly_server entrypoint and health route"
```

---

## Task 3: Dockerfile and `.dockerignore`

Makes the read-only server deployable, with the `CMD` pointed at it. Uses the exact multi-stage uv pattern the spec settled on; `uv.lock` is already committed.

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

**Interfaces:**
- Consumes: `src/readonly_server.py:main` (via `python -m src.readonly_server`); `pyproject.toml`, `uv.lock` (already present).
- Produces: a container image that serves the read-only server on port 8080.

- [ ] **Step 1: Write `.dockerignore`**

`.dockerignore` is security-relevant here, not just build speed — `COPY . /app` would otherwise bake secrets and the never-deploy generic caller into the image. Create `.dockerignore`:

```
.env
.env.*
scripts/
tests/
docs/
.venv/
.git/
.gitignore
**/__pycache__/
.ruff_cache/
.mypy_cache/
.pytest_cache/
*.md
```

Note: excluding `scripts/` is what keeps `scripts/freeagent_api_caller.py` (the read+write generic tool) out of the image. The read-only server does not import from `scripts/`, so the build is unaffected. `[tool.hatch.build.targets.wheel]` packages only `src`, so excluding `tests/` and `docs/` is safe.

- [ ] **Step 2: Write the `Dockerfile`**

Create `Dockerfile` (the spec's confirmed Astral uv pattern; final stage runs the read-only entrypoint as a non-root user):

```dockerfile
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_PYTHON_DOWNLOADS=0
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-editable
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable

FROM python:3.14-slim
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
ENV PATH="/app/.venv/bin:$PATH"
RUN useradd --create-home --uid 10001 appuser
USER appuser
EXPOSE 8080
# Interim read-only server. Switch to `src.server` to deploy the full connector later.
CMD ["python", "-m", "src.readonly_server"]
```

- [ ] **Step 3: Build the image**

Run: `docker build -t freeagent-mcp-remote:readonly .`
Expected: build succeeds through both stages.

- [ ] **Step 4: Confirm the container serves `/health` without credentials**

The spec requires `/health` to succeed without valid FreeAgent/OAuth credentials. `PUBLIC_BASE_URL` is required at startup, so it must be supplied, but no real secrets are needed for the health probe:

```bash
docker run --rm -d -p 8080:8080 \
  -e PUBLIC_BASE_URL=http://localhost:8080 \
  -e FREEAGENT_CLIENT_ID=placeholder \
  -e FREEAGENT_CLIENT_SECRET=placeholder \
  --name fa-readonly-smoke freeagent-mcp-remote:readonly
```

Run (give the server a moment to start, then probe):

```bash
until curl -sf http://localhost:8080/health; do sleep 1; done
```

Expected: `{"status":"healthy","service":"freeagent-mcp-readonly"}`.

Then stop it: `docker stop fa-readonly-smoke`

- [ ] **Step 5: Confirm the generic caller is NOT in the image**

Run: `docker run --rm --entrypoint sh freeagent-mcp-remote:readonly -c 'test ! -e /app/scripts/freeagent_api_caller.py && echo ABSENT'`
Expected: prints `ABSENT` (the never-deploy generic read+write tool did not get baked in).

- [ ] **Step 6: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "feat: containerize the interim read-only server"
```

---

## Task 4: Documentation

Records the interim server as a deliberate, separate deliverable with its bounded security posture, so a future reader (or agent) does not mistake the free-form path for a mistake and "fix" it.

**Files:**
- Modify: `docs/plans/freeagent-mcp-remote.md`
- Modify: `docs/plans/freeagent-mcp-remote-deployment.md`
- Modify: `CLAUDE.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Add the interim-server decision to the spec**

In `docs/plans/freeagent-mcp-remote.md`, add a new subsection under the architecture/decisions area (immediately before the `## Checkable task list` heading) with this content:

```markdown
### Interim read-only server (`src/readonly_server.py`)

A separate, deployable entrypoint from the full connector `create_server()`, added so
there is something usable to connect to before the per-resource tool modules are written.
It exposes exactly one tool, `freeagent_get(path, params)`, GET only.

This deliberately trades the connector's endpoint-per-tool guarantee (one audited endpoint
per tool, IDs validated by `SafeId`) for breadth now. The trade is acceptable *only* for
this interim, single-tenant, read-only server because:

- **Read only.** No write verb is exposed, so no injected instruction can alter the books.
- **Origin guard intact.** Every call routes through `FreeAgentClient`, so a path injected
  via FreeAgent data cannot exfiltrate the bearer token off-origin.
- **Single-tenant.** The only reader is the account owner, reading their own data; the
  residual risk (a read steered to some *other* of the owner's own data) is low.

It is a *separate* entrypoint on purpose: `create_server()` and its
`tests/test_server.py::TestApiCallerIsNotShipped` guarantee are untouched, so the full
connector's stronger guarantee is preserved. `tests/test_readonly_server.py` asserts the
read-only server exposes exactly `freeagent_get` and that it takes no `method` argument.
The deployed container selects which server runs via the Dockerfile `CMD`.
```

- [ ] **Step 2: Add task-list entries to the spec**

In the `## Checkable task list` of `docs/plans/freeagent-mcp-remote.md`, add a new group (place it after the "FastMCP server entrypoint" group):

```markdown
### Interim read-only server
- [x] `src/readonly_server.py`: `register` adds one GET-only `freeagent_get(path, params)` tool; `create_readonly_server()` reuses `build_auth_provider` and `freeagent_token_provider`, adds `/health`; `main()` runs it with `stateless_http=True`
- [x] Separate entrypoint from `create_server()`, so the connector's endpoint-per-tool guarantee and `TestApiCallerIsNotShipped` stay intact
- [x] Tests: exposes exactly `freeagent_get`; the tool takes no `method` argument; the origin guard still blocks an unsafe path; `/health` needs no credentials and `/mcp` rejects unauthenticated requests
- [x] Dockerfile `CMD` runs `src.readonly_server`; `.dockerignore` excludes `scripts/`, so the generic read+write caller is never baked into the image
```

(These are ticked because the plan's earlier tasks complete them. If executing out of order, tick each as its task lands.)

- [ ] **Step 3: Note the deployed entrypoint in the runbook**

In `docs/plans/freeagent-mcp-remote-deployment.md`, add this note immediately after the introductory blockquote at the top:

```markdown
> **Which server is deployed:** the image's `CMD` runs the interim read-only server
> (`src/readonly_server.py`), which exposes a single GET-only `freeagent_get` tool. Every
> step below — Container setup, the FreeAgent OAuth app and its `/auth/callback` redirect
> URI, and all environment variables — is identical for the full connector. Switching to
> the full connector later is a one-line change to the Dockerfile `CMD`
> (`src.readonly_server` → `src.server`) plus a rebuild and redeploy; nothing else here
> changes.
```

- [ ] **Step 4: Note the interim server in CLAUDE.md**

In `CLAUDE.md`, under the `## Architecture` section's `src/` tree description (right after the paragraph about `The request tool must never be registered in src/server.py`), add:

```markdown
**The interim read-only server (`src/readonly_server.py`) is the one intended exception,
and a narrow one.** It is a *separate* deployable entrypoint that exposes a single
GET-only `freeagent_get(path, params)` tool. Its free-form `path` is a deliberate,
documented tradeoff for a single-tenant, read-only interim server (see the spec's "Interim
read-only server" section) — not a violation of the connector's invariant, which is about
`create_server()`. Do not add write verbs to it, and do not merge it into `create_server()`.
```

- [ ] **Step 5: Commit**

```bash
git add docs/plans/freeagent-mcp-remote.md docs/plans/freeagent-mcp-remote-deployment.md CLAUDE.md
git commit -m "docs: record the interim read-only server as a deliberate deliverable"
```

---

## Self-Review

**1. Spec / requirement coverage:**
- (b) deployable → Task 3 (Dockerfile/`.dockerignore`) + runbook note (Task 4). ✓
- (b) read-only → `freeagent_get` uses `client.get` only; `test_the_read_tool_is_get_only` proves no `method` arg. ✓
- (b) "as little work as possible" → one generic tool, reusing all existing auth/client/health infra; no per-resource modules. ✓
- Decoupled from (c) → separate `src/readonly_server.py` entrypoint; `create_server()` and `TestApiCallerIsNotShipped` untouched; `CMD` is the switch. ✓
- Origin guard still protects the free-form path → `test_an_unsafe_path_is_blocked_by_the_origin_guard`. ✓
- `scripts/` never deployed → `.dockerignore` excludes it; Task 3 Step 5 verifies absence in the image. ✓
- Security tradeoff recorded, not hidden → Task 4 (spec section + CLAUDE.md note). ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". Every code and doc step carries the literal content. ✓

**3. Type consistency:** `register(mcp: FastMCP, client: FreeAgentClient) -> None` and `freeagent_get(path: str, params: dict[str, str] | None = None) -> dict[str, Any]` are used identically in Tasks 1–2 and the tests. `create_readonly_server() -> FastMCP`, `main() -> None`, and `READONLY_SERVICE_NAME`/`"freeagent-mcp-readonly"` match between the module, the tests, and the health-route assertion. `client.get(path, params=params)` matches `FreeAgentClient.get(self, path, params=None)` in `src/client.py`. ✓
