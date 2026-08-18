# Parked

Work that has been looked at, understood, and deliberately left for later — with the reason
it can wait written down next to it.

Parking is a decision, not a backlog. Something belongs here when it is a real improvement
that would derail whatever is currently being finished. Something does *not* belong here if
nobody has actually judged it — that is just an idea, and it goes in a conversation, not a
doc people act on.

Tick a box when the item is done. If an item turns out to be wrong, or gets overtaken by
other work, say so on the line rather than deleting it silently — the reasoning is the
useful part.

The items below came out of the code review run on 2026-08-17, immediately before the
repository's first commit. Two findings from that review were fixed at the time rather than
parked (the `.gitignore` credential patterns, and the contradictory redirect-URI
instructions across `README.md` and `.env.example`), so they do not appear here.

## The connector (`src/`)

- [ ] **`verify_token` does not fail closed on a non-JSON 200.** In `src/auth.py`,
  `FreeAgentTokenVerifier.verify_token` calls `response.json().get("user", {})` outside the
  `try`, which only catches `httpx.HTTPError`. A `json.JSONDecodeError` — or an
  `AttributeError`, if the body decodes to a list — escapes the method entirely. A captive
  portal or corporate proxy answering `GET /users/me` with a 200 HTML interstitial would
  therefore 500 inside FastMCP's auth middleware instead of returning `None` and producing
  a clean 401 challenge. The method's own comment says "fail closed"; this path doesn't.
  Wrap the parse and return `None` on failure.

- [ ] **No shared `httpx.AsyncClient`.** `create_server` in `src/server.py` constructs
  `FreeAgentClient` without `http_client`, so `request_raw` opens and closes an
  `AsyncClient` per request. On a scale-to-zero container, a conversation calling three
  tools pays three full TLS handshakes to `api.freeagent.com`. The plumbing for injecting
  one already exists — this is a small change, parked only because it is a performance
  nicety rather than a correctness problem.

- [ ] **Auth and tool logging share one logger name, so their levels can't be set
  independently.** `src/auth.py` and the tool-call middleware in `src/server.py` both use
  the single `logger` (`freeagent_mcp`) defined in `src/log.py`. The misleading
  *tool-shaped* name is no longer the problem (it was renamed from `freeagent_mcp.tools`),
  but the coupling remains: anyone raising that logger to debug tool calls also switches on
  token-verification logging, and vice versa. If that becomes a nuisance, give each area a
  child logger — `freeagent_mcp.auth` and `freeagent_mcp.tools` — under the shared parent.
  Note that `tests/test_server.py` filters on the `freeagent_mcp` name, so check what the
  test asserts before changing it.

- [ ] **`except ValueError, TypeError:` is Python 3.14-only syntax.** In `src/client.py`,
  the unparenthesised form (PEP 758) is correct for this project — `requires-python` is
  `>=3.14` — but it makes the module unparseable by any 3.13-or-earlier tool: an editor's
  bundled interpreter, a CI image that drifts, a contributor's system Python. The failure
  is an opaque `SyntaxError` on the security-critical file rather than a clear version
  error. Parenthesising costs nothing. While there: `TypeError` is unreachable, because the
  value passed is always `response.text` and therefore always a `str`.

## The command-line caller (`scripts/`)

- [ ] **`_settings()` lets the environment override `.env`, contradicting its docstring.**
  In `scripts/freeagent_api_caller.py`, the docstring says it reads `.env` directly rather
  than relying on the environment, but the merge puts `os.environ` last, so the environment
  wins. A developer with a stale `FREEAGENT_DEV_TOKEN` or `FREEAGENT_REFRESH_TOKEN`
  exported in their shell keeps hitting 401s while `fa_auth.py` writes fresh tokens into
  `.env` that are never read — and re-running `fa_auth.py` never fixes it. That is a
  genuinely hard failure to diagnose, which is the reason this one is worth doing early.

  Decide the intent before changing it. The docstring's stated reason is that exported shell
  variables don't reliably survive `fastmcp call` spawning this as a subprocess — which
  argues for `.env` winning. But the merge order may equally have been meant as "let the
  environment override for a one-off run". Those want different fixes: swapping the merge
  order, versus keeping it and correcting the docstring.

- [ ] **`show_headers` silently drops the rate-limit header.** The filter in the `request`
  tool compares `k.title()` against a tuple containing `"X-RateLimit-Remaining"`, but httpx
  lowercases header names and `"x-ratelimit-remaining".title()` is `"X-Ratelimit-Remaining"`
  — a different string. Verified by running it: `x-total-count`, `link` and `content-type`
  come through and the rate-limit one never does, so a user checking their remaining API
  budget concludes FreeAgent didn't send it. Compare case-insensitively.

- [ ] **The OAuth flow has no `state`, and the callback server accepts any code.** In
  `scripts/fa_auth.py`, the authorize URL omits `state`, and `_CallbackHandler.do_GET`
  stores whatever `code` arrives on the loopback port without checking that it belongs to
  this flow. While the five-minute window is open, any local process or any web page open
  in the browser can hit `http://localhost:8723/callback?code=<attacker's code>`; the
  script would exchange it and write a token for someone else's FreeAgent account into
  `.env`, after which the CLI reads the wrong books without saying anything. Generate a
  random `state`, send it, and reject a callback whose `state` doesn't match. Parked as low
  severity because it needs a local attacker inside a narrow window, but it is the only
  item here with a security consequence rather than a usability one.

- [ ] **A rotated refresh token is discarded.** The refresh path in
  `scripts/freeagent_api_caller.py` reads `access_token` from the response but never writes
  a returned `refresh_token` back to `.env`, unlike `scripts/fa_auth.py`, which does persist
  it. If FreeAgent ever rotates refresh tokens on use, the stored one dies after the first
  call and the tool degrades to a confusing 401. Whether FreeAgent actually rotates them is
  unverified — worth checking against the live API, per the `freeagent-api` skill, before
  deciding how much this matters.

- [ ] **Two unhandled decode paths in the `request` tool.** `response.json()["access_token"]`
  in the refresh path raises a bare `KeyError` on a 200 that lacks the key, instead of
  reaching the helpful `RuntimeError` below it. Separately, the response `response.json()`
  sits outside the `freeagent_errors()` block, so a 200 whose body isn't JSON — a proxy
  interstitial, or an endpoint returning CSV or HTML — surfaces a raw `json.JSONDecodeError`
  rather than a `ToolError` with a usable message.

## Docs

- [ ] **The contact section in `README.md` still holds a placeholder.** The "If you get
  stuck" section offers help and then gives no way to get in touch, so the offer reads as
  broken in a readme framed as public. Needs a name and a contact method — a decision for
  the repository's author, not something to invent.

## Open risk to confirm before deploying

- [ ] **Whether FreeAgent tolerates PKCE parameters is unverified.** `OAuthProxy`'s
  `forward_pkce` defaults to `True`, so it forwards PKCE upstream whenever the MCP client
  sends a `code_challenge`, which Claude always does. Nothing in the code or in
  `docs/plans/` records whether FreeAgent's `/approve_app` and token endpoint accept those
  parameters. If FreeAgent rejects the unexpected `code_verifier` at token exchange, the
  connector's consent flow fails at deploy time and no local test would have caught it.
  This is a question to answer, not a defect to fix: confirm against the live API, then
  record the answer in the [main spec](./freeagent-mcp-remote.md) whichever way it goes.
