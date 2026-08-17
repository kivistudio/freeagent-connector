# freeagent-mcp-remote — Tech Spec

> Status: **in progress.** Foundation and the first tool module are built, tested and
> verified; the remaining tool modules are not. See the [task list](#checkable-task-list)
> for exactly what is done.
>
> Confirmed actuals as built: Python 3.14.3 · fastmcp 4.0.0b3 · MCP SDK 2.0.0 ·
> httpx 0.28.1 · pydantic 2.13.4.
> Companion documents: [Tool Inventory](./freeagent-mcp-remote-tool-inventory.md) · [Deployment Runbook](./freeagent-mcp-remote-deployment.md)

## Goal

Build and deploy `freeagent-mcp-remote`: a new, standalone, single-tenant remote MCP server that exposes the FreeAgent accounting API to **Claude web** (and, since it's a standard MCP connector rather than a Claude-specific integration, potentially any other MCP client) over Streamable HTTP, running as a Scaleway Serverless Container.

Existing FreeAgent MCP servers are stdio-only — a client spawns them as a local process, which works for Claude Code/Desktop but not Claude web, since a browser session can only reach a remote connector over a public HTTPS URL. `freeagent-mcp-remote` is built to close that gap: a FreeAgent MCP server reachable remotely, with real OAuth so adding it is just "add a connector."

## Relationship to samaxbytez/freeagent-mcp

This project is an independent implementation, not a fork. It's inspired by, and its FreeAgent API knowledge (endpoints, field names, request-body quirks) is cross-referenced against, [samaxbytez/freeagent-mcp](https://github.com/samaxbytez/freeagent-mcp) — an existing stdio-based FreeAgent MCP server written in TypeScript. To be precise about what that relationship means in practice:

- **Not copied**: no code from that project appears here. Different language (Python vs. TypeScript), different transport (remote HTTP + OAuth vs. local stdio), different everything below the FreeAgent-API layer.
- **Reimplemented**: a handful of pieces of *logic* — the path-traversal/origin-check security guard, ID validation, request param/body building — are deliberately re-created here to do the same job, because they're good ideas worth keeping, expressed fresh in Python rather than translated line-by-line.
- **Cross-referenced, not authoritative**: the FreeAgent API endpoint/field/quirk knowledge in the [Tool Inventory](./freeagent-mcp-remote-tool-inventory.md) doc was verified against that project's source as a secondary check. [FreeAgent's own API docs](https://dev.freeagent.com/docs) are the better secondary source, but they are not authoritative either — they contain gaps, internal contradictions and copy-paste errors. **The live API is the only authoritative source**; check it with the `request` tool in `scripts/freeagent_api_caller.py`.

## Context

Deployment shape: a single-tenant server (one FreeAgent account — the user's own) on Scaleway, as a Serverless **Container**. Two design principles shape every decision below: use current, actively-recommended versions of every runtime/dependency rather than pinning to whatever's convenient today, and reimplement rather than port — pieces are rewritten where a rewrite is cleaner for this project's remote/OAuth architecture, rather than force-fitting patterns designed for a different (local/stdio) deployment shape.

The project is built in Python on [`fastmcp`](https://github.com/PrefectHQ/fastmcp) (PrefectHQ's FastMCP framework, originally created by Jeremiah Lowin), using its `OAuthProxy` for authorization. See [Key decision: OAuthProxy](#key-decision-oauthproxy-as-the-authorization-layer) and [Key decision: Python FastMCP](#key-decision-python-fastmcp) below for the reasoning.

## Architecture overview

```
                    ┌────────────────────────────────────────┐
                    │   Claude web (or any MCP client)        │
                    │   standard "Add connector" OAuth flow   │
                    │   (authorization code + PKCE, MCP-spec  │
                    │    client discovery — CIMD where the    │
                    │    client supports it, else DCR)        │
                    └───────────────────┬──────────────────────┘
                                        │ HTTPS, Streamable HTTP
                                        │ (protocol-defined statelessness,
                                        │  no Mcp-Session-Id)
                                        ▼
                    ┌────────────────────────────────────────┐
                    │  Scaleway Serverless Container            │
                    │  (scale-to-zero, min-scale=0)              │
                    │  freeagent-mcp-remote                      │
                    │                                             │
                    │  Python FastMCP (PrefectHQ/fastmcp)         │
                    │   - OAuthProxy: our server IS the OAuth    │
                    │     authorization server for MCP clients,  │
                    │     AND delegates the upstream grant to    │
                    │     FreeAgent — one browser consent flow   │
                    │   - client_storage: OAuthProxy's own        │
                    │     default (encrypted local file store)   │
                    │     — ephemeral, wiped on every cold start  │
                    │     by design, see storage decision below  │
                    │   - GET /health (custom_route)             │
                    │   - tool modules, one per resource group   │
                    │                                             │
                    │  FreeAgentClient (httpx, async)            │
                    │   - origin-check exfiltration guard         │
                    │   - upstream token via                      │
                    │     get_access_token().token                │
                    └─────────────────────┬──────────────────────┘
                                          │ FreeAgent API calls
                                          ▼
                              ┌─────────────────────┐
                              │ api.freeagent.com   │
                              │  /v2/...             │
                              └─────────────────────┘

  One-time, human, out of band (never runs in the container, no local
  script involved at all):
  1. Register a FreeAgent OAuth app in FreeAgent's dev dashboard, with
     redirect_uri = <CONTAINER_URL>/auth/callback (OAuthProxy's route)
  2. Add the connector in Claude (or any client) → browser opens →
     FreeAgent's real consent screen → approve → done. This IS the
     first-ever authorization; there is no separate developer step.

  Repeats after every cold start (accepted tradeoff, see below): the
  container's local file store is wiped, so step 2 happens again.
```

**Request flow:** Any MCP client's "Add connector" flow triggers a standard OAuth authorization-code exchange against our server's own advertised OAuth endpoints (served by `OAuthProxy`). Because our server has no independent user base of its own, that exchange transparently proxies to FreeAgent's real OAuth: the client is redirected to FreeAgent's consent screen, the user approves, FreeAgent redirects back to our server's callback route, and `OAuthProxy` exchanges the code with FreeAgent, stores the resulting FreeAgent access/refresh token in its `client_storage`, and issues the client its own short-lived FastMCP-signed JWT. From then on, every MCP request carries that FastMCP JWT; `OAuthProxy` validates it, looks up the corresponding stored FreeAgent token, transparently refreshes it if expired, and makes it available to the tool handler via `get_access_token().token`. Every request is self-contained under the current MCP protocol — there's no server-side session to keep warm or lose on scale-to-zero.

There is conceptually **one** OAuth flow with two hops (MCP client ↔ our server, our server ↔ FreeAgent), not two unrelated gates. Successfully completing our server's OAuth flow is how a valid FreeAgent grant gets established, because our server's flow terminates in a real FreeAgent grant — a reader doesn't need to reason about "is this caller allowed to talk to the server" as a separate question from "does the server have a FreeAgent token." The one place a distinction still matters: `OAuthProxy` issues its *own* JWT to the client, decoupled in lifetime from FreeAgent's token — so "is this MCP-client token valid" and "is the underlying FreeAgent token valid" remain two different checks, both handled internally by `OAuthProxy`.

## Key decisions and rationale

This section is the "why" behind choices that aren't obvious from the code alone — written for whoever picks this up later.

### Independent project, not a fork
`freeagent-mcp-remote` is its own repository with its own history, not a git fork of samaxbytez/freeagent-mcp and not a workspace/monorepo alongside it. The two projects are deployment-shape-incompatible in fundamental ways (remote vs. local, one warm process serving many requests vs. one process per session) — different enough that sharing a repo or a package dependency wouldn't buy anything. What's genuinely reusable (see [Relationship to samaxbytez/freeagent-mcp](#relationship-to-samaxbytezfreeagent-mcp) above) is carried over by re-expressing it, not by depending on the other project's package.

### Scaleway Serverless Containers, not Functions
Scaleway Serverless Functions need an event/context adapter shim that an ASGI application (which is what a Python HTTP MCP server is) doesn't speak natively. Containers just run a Docker image listening on a port — a direct match for any ASGI server (e.g. `uvicorn`) serving FastMCP's app.

### Single-tenant: no multi-user database — but note what this does *not* mean
This is a personal tool for one FreeAgent account (the user's own). This rationale is about **our own server not needing to support many different end-users** — it doesn't need per-user identity, session-to-account mapping, or a database of accounts. That is a *separate question* from whether `OAuthProxy` uses DCR/CIMD toward the MCP *clients* connecting to it (Claude, or any other client) — it does, regardless of tenancy, because that's simply how any spec-compliant MCP OAuth server identifies which client is asking. Single-tenancy is about our downstream (FreeAgent) side, not our upstream (MCP client) side.

### Python and package versions deliberately not pinned in this spec
Any specific Python version, `fastmcp`/`httpx`/`pydantic` version number, or Docker base image tag written during planning is a snapshot, not a pin. **Verify at implementation time**: check the current stable Python release (e.g. via `https://www.python.org/downloads/` or `endoflife.date/python`) for the Docker base image tag, and check current versions of every dependency via `uv add`/PyPI before locking. This avoids the spec silently going stale the moment a new Python release or package version ships.

### `client.py`/`utils.py` re-express logic inspired by samaxbytez/freeagent-mcp
[samaxbytez/freeagent-mcp](https://github.com/samaxbytez/freeagent-mcp)'s [`client.ts`](https://github.com/samaxbytez/freeagent-mcp/blob/main/src/client.ts) and [`utils.ts`](https://github.com/samaxbytez/freeagent-mcp/blob/main/src/utils.ts) are small (~200 lines total), zero-dependency, and framework-agnostic — a thin `fetch` wrapper and pure helper functions with no assumptions about where they run. That makes their **logic** (not their syntax) worth reimplementing fresh in Python rather than designing from scratch: the origin-check guard (below), ID validation, and the params/body-building helpers. `httpx` (the standard modern Python async HTTP client — synchronous `requests` doesn't fit an async FastMCP tool handler well) is the natural Python analog of `fetch`. No code is copied — this is a from-scratch Python implementation of the same behavior.

### Security-critical origin-check guard, reimplemented in Python
`FreeAgentClient`'s request method reimplements the path-traversal/origin-check logic from samaxbytez/freeagent-mcp's [`client.ts`](https://github.com/samaxbytez/freeagent-mcp/blob/main/src/client.ts). This stops bearer-token exfiltration via a maliciously injected path — a real risk given that FreeAgent data (which an LLM reads and may act on) could contain an indirect prompt injection attempting to redirect a request to an attacker-controlled host, carrying the FreeAgent bearer token with it. This behavior must never be weakened.

**Verbatim source** (`freeagent-mcp/freeagent-mcp-original/src/client.ts`, lines 96–107 — read from disk directly, not transcribed from memory, so this is exact):

```typescript
const base = this.baseUrl.endsWith("/") ? this.baseUrl : this.baseUrl + "/";
const fullPath = path.startsWith("/") ? path.slice(1) : path;

if (/\.\.[\\/]/.test(fullPath) || /^[a-z]+:\/\//i.test(fullPath)) {
  throw new Error(`Unsafe API path rejected: ${fullPath}`);
}

const url = new URL(fullPath, base);

if (url.origin !== new URL(base).origin) {
  throw new Error(`Resolved URL origin does not match base: ${url.origin}`);
}
```

Three checks, in order, all of which the Python port must reproduce exactly:
1. **Reject `..` followed by `/` or `\`** — regex `/\.\.[\\/]/ ` (note: this is `..` followed by a path separator, not a bare `..` substring — e.g. `..foo` alone would NOT match this regex, only `../foo` or `..\foo` would). Python equivalent: `re.search(r'\.\.[\\/]', full_path)`.
2. **Reject an absolute URL scheme prefix** — regex `/^[a-z]+:\/\//i` (case-insensitive, e.g. `https://`, `ftp://`, `javascript://`). Python equivalent: `re.match(r'^[a-z]+://', full_path, re.IGNORECASE)`.
3. **Resolve the path against the base, then require the resolved origin to exactly equal the base's origin.** JS `new URL(fullPath, base)` + `.origin` comparison. Python's `urllib.parse.urljoin(base, full_path)` + `urlparse(...)` to extract `scheme://netloc` is the direct equivalent — **but verify this in a real test before trusting it**: `urljoin`'s handling of protocol-relative paths (`//evil.com/x`) and `..`-normalization has historically had edge-case differences from JS's `URL` resolution algorithm (WHATWG URL spec vs. Python's RFC 3986-based `urljoin`). Write the exact test cases from `client.ts`'s own test suite (`freeagent-mcp/freeagent-mcp-original/src/client.test.ts` — also on disk) as the Python test's starting point, not fresh ones, so behavior is checked against the same cases the original guard was validated against.

Order matters: checks 1–2 run on the raw un-resolved path (catching obviously-malicious input cheaply, before any URL parsing), check 3 catches anything that resolves to a different origin despite passing 1–2 (the actual enforcement backstop). Preserve this order and the fact that all three are independent gates (any one failing rejects the request), not a single combined condition.

### `safe_id` applied to every path-interpolated ID, including `nominal_code`
Every ID parameter interpolated into a FreeAgent API path must be validated against `^[a-zA-Z0-9_-]+$`, no exceptions, including `nominal_code` (now in the `ledger` module) (samaxbytez/freeagent-mcp leaves this one [unvalidated](https://github.com/samaxbytez/freeagent-mcp/blob/main/src/tools/categories.ts) — a deliberate improvement here). In Python this is most naturally a small reusable validator function (or a `Annotated[str, AfterValidator(...)]` / Pydantic field validator) applied consistently across every tool that takes a path ID — exact mechanism to settle at implementation time, but the regex and the "no exceptions" rule are fixed.

### Line items as real Pydantic models, not JSON-stringified params
FreeAgent's bill endpoints take a nested array of line items. A common but LLM-unfriendly way to expose this as a tool parameter is a JSON-stringified string that the handler parses by hand — a malformed JSON string then produces an opaque parse error instead of a field-level validation error the model can recover from. Here it is a `list[BillItem]`-typed function parameter, where `BillItem` is a small `pydantic.BaseModel`; FastMCP auto-generates the corresponding nested JSON schema from the type hint.

Confirmed: FastMCP's type-hint-to-schema mechanism handles Pydantic models generally (including inside `list[...]`) — its own `ShrimpTank` example shows a nested `list[Shrimp]` field inside a `BaseModel` working via the same `func_metadata()`/`create_model` machinery that processes bare tool parameters, so a top-level `bill_items: list[BillItem]` parameter follows the identical code path. No official example shows that exact "bare top-level `list[Model]` parameter" shape verbatim, but the mechanism is source-confirmed, not a guess.

**Scope note (supersedes earlier planning):** bills are now the *only* resource in scope with a line-items array. Invoices, estimates and credit notes were dropped when the tool surface was re-scoped around bookkeeping and tax rather than sales workflow — see the [Tool Inventory](./freeagent-mcp-remote-tool-inventory.md). Their field names are therefore no longer something this project needs to confirm.

Confirmed bill line-item fields: `category`, `description`, `total_value`, `sales_tax_rate`. Note the update protocol is three-way and destructive if misunderstood (`url` + fields edits, `_destroy: 1` removes, `url: ""` adds, and omitting an item does *not* delete it) — detail in the tool inventory.

### Key decision: OAuthProxy as the authorization layer

The user's requirement: adding this connector to any MCP client should be a normal "click Connect, see a consent screen, approve" experience — no local script, no pasted URL, no shared secret to copy around. MCP's authorization spec explicitly supports "third-party service flows" for exactly this shape, and `fastmcp`'s `OAuthProxy` is the purpose-built implementation of it: the MCP server acts as an OAuth authorization server toward MCP clients, while internally delegating the actual authorization to FreeAgent (which doesn't support Dynamic Client Registration or Client ID Metadata Documents itself).

**`OAuthProxy`'s real constructor signature** (confirmed directly from source: `fastmcp_slim/fastmcp/server/auth/oauth_proxy/proxy.py`, class `OAuthProxy(OAuthProvider, ConsentMixin)`, all params keyword-only):

```python
def __init__(
    self,
    *,
    upstream_authorization_endpoint: str,
    upstream_token_endpoint: str,
    upstream_client_id: str,
    upstream_client_secret: str | None = None,
    upstream_revocation_endpoint: str | None = None,
    token_verifier: TokenVerifier,
    base_url: AnyHttpUrl | str,
    resource_base_url: AnyHttpUrl | str | None = None,
    redirect_path: str | None = None,               # defaults to "/auth/callback" when None
    issuer_url: AnyHttpUrl | str | None = None,
    service_documentation_url: AnyHttpUrl | str | None = None,
    allowed_client_redirect_uris: list[str] | None = None,
    valid_scopes: list[str] | None = None,
    forward_pkce: bool = True,
    forward_resource: bool = True,                   # RFC 8707 resource indicator
    token_endpoint_auth_method: str | None = None,
    extra_authorize_params: dict[str, str] | None = None,
    extra_token_params: dict[str, str] | None = None,
    client_storage: AsyncKeyValue | None = None,      # None → encrypted local file store
    jwt_signing_key: str | bytes | None = None,       # None → derived from upstream_client_secret via HKDF
    require_authorization_consent: bool | Literal["remember", "external"] = True,
    consent_csp_policy: str | None = None,
    fallback_access_token_expiry_seconds: int | None = None,
    fallback_refresh_token_expiry_seconds: int | None = None,
    fastmcp_access_token_expiry_seconds: int | None = None,
    token_expiry_threshold_seconds: int = 0,
    enable_cimd: bool = True,
    identity_assertion: IdentityAssertion | None = None,
):
```

`upstream_authorization_endpoint`/`upstream_token_endpoint`/`upstream_client_id`/`upstream_client_secret` map directly to FreeAgent's OAuth app credentials and endpoints. Required, no default: `upstream_authorization_endpoint`, `upstream_token_endpoint`, `upstream_client_id`, `token_verifier`, `base_url`.

**`OAuthProxy` manages upstream (FreeAgent) token storage and refresh itself, and exposes it to tool handlers.** The proxy receives the upstream token, stores it via `client_storage`, issues its own FastMCP-signed JWT to the client (decoupling client-facing token lifetime from FreeAgent's), and on each request validates the FastMCP JWT, retrieves the stored upstream token, and refreshes it transparently when expired. Confirmed pattern, from FastMCP's own OCI integration docs (`docs/integrations/oci.mdx`), for extracting the raw upstream bearer token inside a tool:

```python
from fastmcp.server.dependencies import get_access_token


@mcp.tool
async def freeagent_get_company() -> dict:
    access_token = get_access_token()
    token = access_token.token  # the raw FreeAgent bearer token string
    # pass `token` to FreeAgentClient as the Authorization: Bearer value
```

`get_access_token()` returns an object with `.token` (raw upstream bearer string) and `.claims` (decoded JWT claims). This project does not build a custom on-demand-refresh-with-mutex module — `OAuthProxy` owns that responsibility entirely; `FreeAgentClient`'s token provider is a thin call to `get_access_token().token`.

**`OAuthProxy` supports CIMD, `enable_cimd=True` by default.** DCR-based clients remain supported alongside it (DCR is deprecated at the MCP spec level with a 12-month window, not removed) — `enable_cimd` adds CIMD as an additional path, it doesn't disable DCR.

**Storage: `OAuthProxy`'s own default, not a custom backend.** Leaving `client_storage=None` gives an encrypted local file store, created automatically in the container's data directory — no code to write, no external service to provision. **Deliberately chosen over durable external storage** (Scaleway Object Storage, Secret Manager, Redis, or a database were all considered and rejected): the local file store is wiped on every cold start, since this container runs with `min-scale=0` (scale-to-zero) and has no persistent disk across restarts. That means both the FreeAgent token *and* any MCP client's DCR/CIMD registration are lost on every cold start, requiring the connector to be reconnected — this is an accepted tradeoff for a single-tenant personal tool, not an oversight. See the [Deployment Runbook](./freeagent-mcp-remote-deployment.md) for what re-authorization actually looks like in practice, and revisit this decision (swap in a durable `AsyncKeyValue` backend, or move to `min-scale=1`) if the reconnect frequency turns out more disruptive than expected once in real use.

One incidental robustness note, not load-bearing given the above: `jwt_signing_key`, if left unset, is deterministically derived from `upstream_client_secret` via HKDF rather than randomly generated — so previously-issued JWT *signatures* would in principle stay valid across a restart even without a fixed key. This doesn't rescue the chosen design, though, since `client_storage` itself (where the JWT-to-upstream-token mapping lives) is still wiped regardless — worth knowing, not worth relying on.

**What's still manual, and genuinely outside MCP's control:**
- **Registering the FreeAgent OAuth app** in FreeAgent's own developer dashboard, including setting its `redirect_uri` to point at the deployed container's `OAuthProxy` callback route (`<CONTAINER_URL>/auth/callback` by default) — FreeAgent doesn't speak DCR/CIMD, so someone has to do this once, by hand, in FreeAgent's UI.
- Everything else — the actual authorize-and-store-tokens flow — is automatic and happens every time a client connects (first time, and again after any cold start, per the storage decision above).

**Confirmed**: Claude.ai's custom connector flow ("Add custom connector" → enter URL → optionally supply OAuth credentials) auto-detects a standards-compliant OAuth MCP server via the spec's `401` + `WWW-Authenticate` + protected-resource-metadata discovery chain — there's no manual "select auth type" step, and OAuth credential entry is explicitly optional (only needed for confidential-client auth or a stable pre-registered client instead of relying on DCR/CIMD). Source: Anthropic's connector authentication and custom-connector docs, fetched directly.

### Key decision: Python FastMCP

The user already uses [`fastmcp`](https://github.com/PrefectHQ/fastmcp) in another project — a real consistency win, one mental model across repos. It's also a mature implementation of the OAuth-delegation feature this project depends on most: it's on major version 4 (`v4.0.0b3` beta as of this writing, `v3.4.7` the latest parallel-maintained stable line), with a dedicated `OAuthProxy`/`RemoteAuthProvider`/`JWTVerifier` ecosystem.

Confirmed API shapes, from FastMCP's own docs/source:
- **Tool registration**: `@mcp.tool` (bare) or `@mcp.tool(description=...)` decorating a plain function. Confirmed: FastMCP "parses the function's docstring for the tool description and, if present, per-parameter descriptions"; an explicit `description=` overrides the docstring for the tool description, though docstring-derived parameter descriptions still apply. Source: `gofastmcp.com/servers/tools`.
- **Stateless HTTP**: `stateless_http=True` (passed to `run()`/`streamable_http_app()`) is FastMCP's supported mode for a scale-to-zero container. Under the current protocol revision (see below) this is close to the protocol's own default behavior for a server with no session-dependent features, but it's set explicitly regardless for interoperability with clients on an earlier protocol revision.
- **Custom routes** (`/health`): confirmed exact pattern, from `gofastmcp.com/deployment/http` (not `/servers/http` — that path 404s):
  ```python
  from starlette.responses import JSONResponse


  @mcp.custom_route("/health", methods=["GET"])
  async def health_check(request):
      return JSONResponse({"status": "healthy", "service": "freeagent-mcp-remote"})
  ```
  Custom routes are explicitly never protected by the server's auth middleware (confirmed in docs) — appropriate for a health check. `OAuthProxy` registers its own callback route (`redirect_path`, default `/auth/callback`) automatically as part of constructing it, so there's no hand-written callback handler to write at all.
- **Tool handler return values**: confirmed — plain Python values (`str`, `bytes`, `dict`/`list`, or `fastmcp.utilities.types.Image`) are automatically converted into the appropriate MCP content block; no manual wrapping needed.
- **Error-raising convention**: confirmed — raise `fastmcp.exceptions.ToolError` (`from fastmcp.exceptions import ToolError; raise ToolError("...")`). Its message is always sent to the client regardless of any `mask_error_details` setting, unlike a plain unguarded exception, which may get its message masked. `FreeAgentClient`'s error-propagation contract should raise `ToolError` (or a `FreeAgentApiError` that tool handlers catch and re-raise as `ToolError`), not return an error-shaped value.
- **Tooling**: `pytest` for tests; **`uv`** (Astral's Rust-based Python package/project manager) for dependency management; **`ruff`** for linting and formatting; a Python base image (`python:<version>-slim` — exact tag TBD, see the version-pinning note above) for the Dockerfile.
- **`httpx`** for `FreeAgentClient` — the standard modern async-capable HTTP client for Python.

**Version: `fastmcp` v4 (pre-release), not the v3 stable line.** Confirmed current versions: `4.0.0b3` (Aug 14) is the latest v4 beta; `3.4.7` (Aug 10, a security fix for OIDC `private_key_jwt` auth) is the latest v3 stable, still actively maintained in parallel. This project deliberately takes the v4 pre-release, and this is now more than an acceptable-risk bet — it's confirmed the right call: the underlying MCP Python SDK is **already on its stable 2.0 line and speaks the 2026-07-28 stateless protocol natively** (not forward-looking), and `server/discover` has a real implementation plus dedicated tests in the FastMCP repo (`test_protocol_eras.py`, `test_mode_negotiation.py`, `test_discovery_middleware.py`), not just a changelog mention. FastMCP's own dev notes do flag some known gaps in full feature parity across the modern stateless path (a few session-dependent features like `ctx.session_id`/`ctx.set_state` don't fully round-trip yet) — none of which this project uses. **Still check the exact current `v4` version at implementation time** (pre-1.0, expect API changes between beta releases) rather than assuming `v4.0.0b3` specifically.

### MCP protocol revision (2026-07-28) and what it means for this design

The current MCP specification (2026-07-28) is a major revision: it removes protocol-level sessions and the `Mcp-Session-Id` header entirely, along with the `initialize`/`notifications/initialized` handshake — every request is now self-contained, carrying protocol version and capabilities in `_meta`. This project's tools never needed session state in the first place (no elicitation, sampling, or resource subscriptions), so it's a natural fit for how the protocol works now; `stateless_http=True` is set explicitly regardless, for interoperability with any client still on an earlier protocol revision.

Other relevant points from this revision:
- **Servers MUST implement a new `server/discover` RPC** advertising supported protocol versions/capabilities/identity. Not hand-implemented by this project — confirmed already implemented and tested in FastMCP itself (dispatches to `on_discover`, validated via `mcp_types.DiscoverRequest`/`DiscoverResult`), the same way it already handles `initialize`.
- **DCR is deprecated** (12-month window, still functional) in favor of CIMD for client registration — directly relevant to `OAuthProxy` above: CIMD support (`enable_cimd=True`) is already present and confirmed, so no extra work is needed here.
- **HTTP+SSE transport is now fully deprecated** — consistent with the Streamable HTTP choice already made.
- **Roots, Sampling, and Logging features are deprecated; MRTR (multi-round-trip requests) replaces server-initiated elicitation/sampling patterns.** None of this touches `freeagent-mcp-remote` — this project has no elicitation, sampling, roots, or resource-subscription needs, and none are planned. Recorded here explicitly so a future reader can tell this area of the spec was considered and ruled out as not applicable, not missed.

## What each file is responsible for

| File (Python) | Responsibility |
|---|---|
| `src/client.py` | `FreeAgentClient` — thin `httpx`-based wrapper around the FreeAgent API, with the origin-check security guard |
| `src/utils.py` | `safe_id`, response/error helpers, `build_params`, `build_body`, `log_tool_call` |
| `src/tools/*.py` | One module per FreeAgent resource group; each exports `register(mcp, client)` and defines `@mcp.tool`-decorated functions |
| `src/auth.py` | Builds and configures the `OAuthProxy` instance (upstream FreeAgent endpoints/credentials; `client_storage` and `jwt_signing_key` left at their defaults per the storage decision above) |
| `src/server.py` | Builds the `FastMCP` instance with `auth=<OAuthProxy instance>`, registers `/health` via `custom_route`, registers every module in `TOOL_MODULES`, runs with `stateless_http=True` |

There is no local authorization script in this design, and no storage module to build — `OAuthProxy`'s browser-based consent flow and its own default local file store are the entire authorization mechanism.

`build_body` standardizes on one idiom for building JSON request bodies (keeps values un-stringified, strips `None`) — a new helper, added so every tool file in this project follows one consistent pattern for building request bodies.

## Testing conventions

- Tests split **one file per resource** from the start (`test_bills.py`, `test_invoices.py`, etc.). Every tool file has full coverage from the moment it's created.
- `pytest` + `pytest-asyncio` with `asyncio_mode = "auto"` in `pyproject.toml` — confirmed the current recommendation specifically for asyncio-only projects (this one doesn't mix in trio/anyio), per `pytest-asyncio`'s own docs; `strict` mode with explicit `@pytest.mark.asyncio` remains the library default and is for mixed-backend projects, not needed here.
- HTTP-layer mocking: `respx` — confirmed still current and maintained (latest release April 2026), and still the option `httpx`'s own official docs point to for realistic test suites over the built-in `httpx.MockTransport` (which suits only trivial single-handler cases).
- FastMCP's in-memory test `Client` — confirmed real, documented at `gofastmcp.com/patterns/testing`:
  ```python
  from fastmcp.client import Client


  @pytest.fixture
  async def mcp_client():
      async with Client(transport=mcp) as client:
          yield client


  # await mcp_client.list_tools()
  # await mcp_client.call_tool(name="freeagent_get_company", arguments={})
  ```
- A shared `tests/conftest.py` (not itself a test file) provides fixtures for a mock `FreeAgentClient` and the in-memory `Client` fixture above. All 14 tool-module test suites import these fixtures.
- Standard per-file assertions: a "registers N tools" count check (via the FastMCP server's tool listing), one happy-path assertion per tool verifying the exact underlying HTTP call and args, and one error-path case (asserting a `ToolError` is raised).
- `ruff check` and `ruff format` for linting/formatting; a type checker (`mypy` or `pyright` — **check what the user's other Python FastMCP repo already uses, and mirror it for consistency, at implementation time**).

## Checkable task list

Organized by component. Each item is one meaningful deliverable.

### 0. Scaffold
- [x] `git init`, `pyproject.toml` (uv, ruff, `pytest-asyncio` `asyncio_mode = "auto"`, mypy strict), `.gitignore`, `.env.example`
- [x] Current stable Python determined at implementation time: **3.14.3**
- [x] Dependency versions resolved: `fastmcp==4.0.0b3`, `httpx 0.28.1`, `pydantic 2.13.4`, `respx`, `uvicorn`
  - **Gotcha worth remembering:** fastmcp v4 being a pre-release forces uv's `prerelease = "allow"` globally, which silently pulled httpx onto `1.0.dev3` and pydantic onto `2.14.0b1`. The `<1.0.dev0` / `<2.14.0a0` upper bounds in `pyproject.toml` are load-bearing, not cosmetic.
- [x] `CLAUDE.md` written for the repo
- [x] `uv sync`, `ruff check` and `mypy` all clean
- [ ] Initial commit

### 1. Client and utils
- [x] `src/client.py` (`FreeAgentClient`, `FreeAgentApiError`, `UnsafePathError`) with the origin-check guard
  - Python's `urljoin` behaviour was verified empirically rather than assumed. Result: `//evil.com/x` is defused by the leading-slash strip and stays on our origin, but `///evil.com/x` genuinely resolves to another host and is caught **only** by the origin comparison. Both are locked in as tests.
  - No form-encoded helpers: form bodies were only ever for OAuth token exchange, which `OAuthProxy` now owns.
- [x] `src/utils.py` (`safe_id`, `SafeId`, `build_params`, `build_body`, `log_tool_call`)
  - `build_params` renders booleans as `true`/`false`; Python's `str(True)` gives `"True"`, which FreeAgent rejects.
- [x] `build_body` added as a new shared helper; preserves falsy zeros, serialises Pydantic models
- [x] Test coverage for both, including every security-guard rejection case

### 2. OAuthProxy
- [x] `src/auth.py` builds the `OAuthProxy`; `client_storage` and `jwt_signing_key` left at defaults per the storage decision
- [x] `FreeAgentTokenVerifier` validates FreeAgent's opaque tokens against `GET /users/me`, following FastMCP's own GitHub-provider pattern; fails closed on network error
- [x] OAuth endpoints derived from the API base URL, making prod-OAuth-against-sandbox-API unrepresentable
- [x] `FreeAgentClient`'s token provider wired to `get_access_token().token`
- [x] Test coverage for the wiring

### 3. Tool layer
Full endpoint/field/quirk detail: [Tool Inventory](./freeagent-mcp-remote-tool-inventory.md). Build order follows the phases defined there.

- [x] Registration pattern established on `company.py` — modules export `register(mcp, client)`; the client is injected rather than imported so tests can mock its HTTP layer
- [ ] **Phase 1**: `reports`, `ledger`, `banking`, `reconciliation` — read the books, then reconcile them; includes both write paths
- [ ] **Phase 2**: `bills`, `expenses`, `assets`
- [ ] **Phase 3**: `taxes`, `payroll`, `year_end`
- [ ] **Phase 4**: `timeslips`, `tasks`, `projects`, `users`, `contacts`
- [ ] Apply `SafeId` to every path-interpolated ID without exception, including `nominal_code` and date-keyed `period_ends_on` segments
- [ ] One test file per module, all passing together

### 3a. Local development tooling
- [x] `scripts/freeagent_api_caller.py` — local-only MCP server exposing one generic `request` tool, so tool shapes are designed against real data rather than guessed. Routes through `FreeAgentClient`, so exploration exercises the same origin guard as production.
- [x] `scripts/fa_auth.py` — one-time OAuth bootstrap writing a development token to `.env`
- [x] `.claude/skills/freeagent-api/SKILL.md` — tells Claude when and how to use the above
- [x] Boundary enforced by tests: the production server exposes no generic request tool, and no production tool accepts a free-form `path` or `method`
- [ ] Answer the open API questions against the live account. Remaining: split/partial bank explanations; the `/accounting/transactions` 12-month cap; which report carries `retained_profit_carried_forward`; whether explanations accept attachments; whether the documented pagination defaults (25/max 100) hold on every endpoint.
  - [x] Pagination *mechanics* confirmed 2026-08-17 via `/contacts`: `per_page` honoured, `X-Total-Count` accurate, `Link` present — **but with single-quoted `rel='next'`**, not the RFC-standard double quotes, which will silently break a naive pagination parser.

### 4. FastMCP server entrypoint
- [x] `src/server.py`: `FreeAgentClient` built once at startup, one `FastMCP` instance with `auth=<OAuthProxy>`, `GET /health` via `@mcp.custom_route`, modules registered from `TOOL_MODULES`, runs with `stateless_http=True`
- [x] `FREEAGENT_DEV_TOKEN` local escape hatch — opt-in only, with a test asserting it does nothing unless explicitly set
- [x] Test coverage against the real ASGI app: `/health` needs no credentials; `/mcp` rejects unauthenticated requests with a `WWW-Authenticate` challenge; the advertised protected-resource metadata document is followed and confirmed to exist
  - Confirmed live: the challenge points at `/.well-known/oauth-protected-resource/mcp`, and `OAuthProxy` auto-registers `/auth/callback`.

### 5. Dockerfile and container build
- [ ] Write `.dockerignore` and a multi-stage `Dockerfile` following Astral's confirmed current pattern:
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
  COPY --from=builder /app/.venv /app/.venv
  # run as non-root, EXPOSE 8080, CMD the ASGI app (uvicorn/FastMCP's own runner)
  ```
  Python 3.14 confirmed as current stable at implementation time; re-check before building.

  **`.dockerignore` is security-relevant here, not just a build-speed concern.** `COPY . /app` would otherwise bake into the image:
  - `.env` — real FreeAgent client secret and a development token
  - `scripts/freeagent_api_caller.py` — the generic `request` tool, which must never be reachable in a deployed context
  - `.venv/`, caches, `docs/`, `tests/`

  Exclude all of the above explicitly.
- [ ] Build locally and confirm `docker run` starts and `/health` succeeds without requiring valid FreeAgent/OAuthProxy credentials to be present

### 6. Deployment
- [ ] Follow the [Deployment Runbook](./freeagent-mcp-remote-deployment.md): Container Registry namespace, image push, Container namespace/container (`min-scale=0`, `max-scale=1`), FreeAgent OAuth app registration + redirect URI, environment variables, deploy, add the connector in Claude (or any MCP client)

### 7. End-to-end verification
- [ ] Local Docker smoke test: `/health` succeeds unauthenticated; an unauthenticated `POST /mcp` is rejected with a proper OAuth challenge (401 + `WWW-Authenticate`, not a bare 401 — confirm the exact spec-required response shape at implementation time)
- [ ] Repeat against the deployed Scaleway Container
- [ ] Live verification via an MCP client: add the connector, complete the browser OAuth consent flow (through to FreeAgent's real consent screen and back), confirm tools are listed, call `freeagent_get_company` (zero-side-effect read) and a filtered list call (e.g. `freeagent_list_projects` with a `view` param), confirm real data comes back
- [ ] Cold-start behavior check specific to this design: force the container to scale to zero (or manually restart it), then make another tool call without reconnecting — confirm what actually happens (does the client silently re-prompt for consent, or does it show a broken-connector error requiring manual removal/re-add?). This is exploratory, not a pass/fail test — the accepted tradeoff means *some* form of reconnection is expected; the goal is understanding which form, so it's not a surprise in real use
- [ ] Note for future reference: Claude.ai/Desktop's tool-call timeout is 300 seconds and its max tool result size is ~150,000 characters (re-verify current limits at implementation time). Every tool here is a single fast FreeAgent API round-trip well under that size, so no pagination/truncation handling is needed at this stage — revisit if a `list_*` tool is ever called against an account with thousands of records
