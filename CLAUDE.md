# CLAUDE.md

Project guidance for Claude Code working in this repository.

## What this is

`freeagent-mcp-remote` — a remote MCP server exposing the
[FreeAgent](https://dev.freeagent.com/) accounting API to Claude web and other
MCP clients over Streamable HTTP. Python, deployed as a Scaleway Serverless
Container. Single-tenant (one FreeAgent account).

**Two deliverables, at different stages.** The connector (`src/`) is the goal
and is not finished. The command-line API caller
(`scripts/freeagent_api_caller.py`) works today, is documented in the README as
a usable tool, and is *also* how the connector's tools get designed against real
data. Don't describe the caller as throwaway development tooling — it is a
supported thing people are told to use. Its one hard constraint is that it is
**never deployed**, which is about shipping, not about who may run it.

**Before touching anything, read `docs/plans/freeagent-mcp-remote.md`** — the
full tech spec: architecture, every key decision with its rationale, and the
checkable implementation task list. This file is a quick-reference summary of
it, not a replacement for it.

## Architecture

```
src/
├── client.py     # FreeAgentClient — httpx wrapper, with the origin-check security guard
├── utils.py      # safe_id, build_params, build_body, response/error helpers
├── log.py        # The shared application logger (freeagent_mcp)
├── auth.py       # Builds the OAuthProxy instance (FreeAgent upstream config)
├── server.py     # FastMCP instance, /health route, tool registration, stateless_http=True
└── tools/        # One file per resource group; each exports register(mcp, client)

scripts/
├── freeagent_api_caller.py  # NEVER DEPLOYED. One generic `request` tool, driven from
│                            # the command line via `fastmcp call`. Routes through
│                            # FreeAgentClient, so it exercises the same origin guard.
└── fa_auth.py               # One-time browser OAuth login, writes a token to .env
```

**The `request` tool must never be registered in `src/server.py`.** The
connector's safety rests on each tool exposing one endpoint with its IDs
validated by `SafeId`; a generic "call any endpoint" tool hands arbitrary API
access to a model reading data that may carry injected instructions.
`tests/test_server.py::TestApiCallerIsNotShipped` enforces this — don't weaken
it.

## Tool pattern

Every resource file in `src/tools/` exports `@mcp.tool()`-decorated async
functions. Each handler:

1. Uses `FreeAgentClient` to hit the API with a **relative** path.
2. Returns plain Python values (dict/list) — FastMCP wraps them into MCP
   content blocks automatically.
3. Raises `fastmcp.exceptions.ToolError` on failure, not a returned
   error-shaped value.
4. Uses `build_params(...)` for query strings, `build_body(...)` for request
   bodies (wrapped in the resource's singular key, e.g.
   `{"contact": build_body({...})}`).

Handlers do **not** log themselves. Every `tools/call` is logged centrally by
`StructuredLoggingMiddleware`, wired once in `src/server.py`, so a new module
cannot forget to log.

**IDs are interpolated into request paths**, so every ID parameter MUST use
`safe_id` validation (regex `^[a-zA-Z0-9_-]+$`).

**Line items** (bill nested arrays) are real `list[SomePydanticModel]`
parameters, never a JSON-stringified string param.

**Full-URL reference params are not path IDs.** FreeAgent cross-references
resources by URL, and those appear in query strings and bodies, where
`client.py`'s origin guard — which guards the _path_ — does not reach. Tools
take bare `SafeId` values and build the URL server-side.

Full per-resource endpoint/field/quirk detail:
`docs/plans/freeagent-mcp-remote-tool-inventory.md`.

## Security conventions (this repo's threat model is indirect prompt injection)

- **IDs** → always `safe_id` (blocks path traversal via ID fields).
- **`client.py`'s origin-check guard is the critical boundary** — never weaken
  it. It rejects `../`-containing paths, rejects absolute-URL-scheme paths,
  and verifies the resolved URL's origin matches the FreeAgent API base
  origin exactly. This stops the bearer token being sent off-origin via a
  path injected through FreeAgent data an LLM reads back. See the spec's
  "Security-critical origin-check guard" section for the verbatim original
  logic this must reproduce.
- **Token handling is `OAuthProxy`'s responsibility, not this codebase's.**
  No hand-rolled token storage, refresh, or file persistence — the FreeAgent
  bearer token is retrieved per-call via `get_access_token().token` and never
  logged, echoed into errors, or persisted by application code.
- **No new sinks**: any new outbound request, or any other consumer of model-
  or FreeAgent-data-derived input, needs the same scrutiny as `client.py`'s
  existing guard.
- **Error messages** don't leak secrets or full tokens.

## Conventions

- **Package management**: `uv`. **Linting/formatting**: `ruff`.
- **Tests**: `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`), `respx` for
  mocking `httpx`, one test file per resource (`test_bills.py`, etc.), FastMCP's
  in-memory `Client` for tool-invocation tests.
- **Commits**: Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`,
  `refactor:`, `test:`, `ci:`, `perf:`).

## Check FastMCP first, before hand-rolling

FastMCP is the framework this server is built on, and it does far more than register
tools — it has middleware, structured logging, error handling, context/state, auth
integrations and more. **Before reaching for general Python knowledge to build a mechanism
by hand, check whether FastMCP already provides it.** Consult the docs at
<https://gofastmcp.com/> (and the installed version's own API — note we pin a pre-release,
`fastmcp==4.0.0b3`, so a docs feature may differ or not exist yet in our version; confirm
against what's installed). Default to "does the framework own this?" rather than "how
would I normally do this in Python?".

Reinventing something the framework owns means more code to maintain and code that drifts
from the framework's conventions and lifecycle. Tool-call logging is the worked example:
it started as a hand-rolled `log_tool_call` called manually in every handler, and is now
FastMCP's `StructuredLoggingMiddleware` registered once in `src/server.py` — one place
instead of a call a new handler could forget. Where the framework genuinely doesn't cover
a need, hand-rolling is fine — but establish that first, don't assume it.

## Keeping the plan docs current

`docs/plans/` is reference material that people act on, so stale content there is worse
than no content. **Update the docs in the same change that makes them wrong**, not later.

### Write docs that don't go stale

**Never write anything that a single addition makes wrong.** Exact counts are the main
offender — "73 tools across 16 modules" is wrong the moment one tool is added or dropped,
and nobody remembers to update it. Prefer:

- "the modules listed below" over "16 modules"
- "the tools in this table" over "26 tools"
- referring to a phase by name over by size

The same applies to anything positional ("the third section"), anything implying
completeness ("the full set"), and anything time-relative ("currently", "recently",
"now"). Write absolute dates when a date is needed.

### The tool inventory is a guide, not a specification

It records what research suggests, not a contract to fulfil. Keeping its labels honest
matters far more than keeping any total accurate.

Entries are labelled on **two independent axes**. Don't collapse them — "we've decided to
build this" and "we know what it actually returns" are different facts, and a module can
easily be the first without being the second.

**Axis 1 — scope.** Are we building it?

| Label | Means |
|---|---|
| **Recommended** | Research suggests it's worth building. Not yet decided. |
| **Confirmed** | Decided: it's in scope. Says nothing about whether the shape is known. |
| **Recommended to exclude** | Suggested to leave out, with the reason recorded so it isn't re-litigated. |
| **Confirmed not needed for now** | Decided: out of scope. May become relevant later. |

**Axis 2 — verification.** Do we know what the API actually returns?

| Tag | Means |
|---|---|
| **Shape confirmed** | Checked against the live API with `request`. Note when, and anything that contradicted the docs. |
| *(no tag)* | Endpoint, fields and behaviour come from FreeAgent's docs only. Treat as unverified. |

Most entries are scope-**Confirmed** but carry no shape tag — that combination is normal
and is exactly what the `freeagent-api` skill exists to resolve. Add **Shape confirmed**
only for something you actually called.

Moving between labels is normal and expected. Deciding not to build a *Recommended* item
is not a deviation from the plan — the plan was a recommendation.

### When to update what

- **finish a task** → tick its box in the spec's task list, and record anything surprising
  you learned doing it. The gotchas are the most valuable part of that list.
- **change an environment variable name** → update the deployment runbook. It once said
  `SERVER_BASE_URL` while the code read `PUBLIC_BASE_URL`, which would have killed a
  deploy for a non-obvious reason.
- **change scope** → say so explicitly in the affected doc rather than quietly editing
  around it, so a future reader can tell a decision was revisited rather than missed.
- **confirm or rule out something previously unverified** → move it to the right label and
  say how you established it.
- **park something** → add it to `docs/plans/parked.md` (see below).
- **fix a parked item** → tick its box there.

If a doc and the code disagree, the code is the truth — fix the doc.

### `docs/plans/parked.md`

The home for work that has been looked at and deliberately deferred: a checkbox per item,
each with the reason it can wait. It exists so that noticing a real problem mid-task doesn't
force a choice between derailing the current task and losing the finding.

Write the *reason* and the *failure it causes*, not just the fix — an entry a future reader
can't evaluate is an entry they'll either re-investigate from scratch or ignore. Anchor each
one to a file and a function or symbol rather than a line number; line numbers rot on the
next edit.

Only park things that have actually been judged. An unexamined idea is not parked, it's just
an idea, and putting it here dilutes a list people are meant to trust. Nothing goes in
without the author's say-so.

## Writing for humans, not developers

`README.md` and anything else public-facing is written for **non-technical readers
first**. Most GitHub repos are written for developers and assume a lot; this one
shouldn't.

- Explain what something _is_ before using its name. "MCP server" means nothing to a
  reader who hasn't met the term — say what it does, then name it.
- Prefer concrete examples over capability lists.
- Keep developer material in a clearly marked section so a non-developer can stop reading
  at the right point.

**Never use advertising language.** No "empower", "leverage", "seamless", "powerful",
"unlock", "transform", "solutions", "take your X to the next level", no exclamation
marks, no calls to action. The contact section is a plain offer of help from a person who
happens to do this for a living — if a sentence would fit in a landing page, rewrite it.

## Writing specs and code comments

The previous section is about public-facing copy. Internal technical writing — the plan
docs in `docs/plans/` and `docs/superpowers/plans/`, and code comments — has a different
reader: **an experienced engineer who is rusty on Python and new to this stack.** Assume
fluency in software generally (design, testing, HTTP, security models, async as a concept)
— don't explain what a decorator, a context manager or dependency injection *is*. Do
explain, at first appearance:

- **Python-specific idioms and syntax** a strong generalist may not recall — what
  `@mcp.tool` actually does to the function it wraps, why `async with` here, what
  `Annotated[str, AfterValidator(...)]` buys us, `from __future__ import annotations`,
  `**kwargs` conventions, and `uv`/packaging behaviour.
- **Stack-specific concepts** — FastMCP, `OAuthProxy`, MCP protocol details, `respx`,
  pytest fixtures — the first time each shows up.

This is the same gap described in "Working with this repo's author" below — it applies to
written artifacts and live conversation alike.

The test: a comment or spec passage earns its place if it saves a capable-but-rusty reader
a trip to the docs, and wastes space if it explains something they'd know from any
language. Prefer *why* over *what* — the code already says what it does; the comment says
why it's done this way. The existing comments in `src/client.py` and `src/auth.py` are the
model to match.

## Working with this repo's author

- **First time building an MCP server end to end in Python.** Explain Python and MCP
  choices that a first-timer wouldn't have met before — decorators doing non-obvious
  work, async patterns, packaging behaviour, protocol concepts. Don't explain general
  programming; the gap is specific to this stack, not to software.
- **ADHD, and scope bleed is a known pattern.** Actively hold the line:
  - When a new idea arrives mid-task, say plainly that it's a change of scope, note it in
    `docs/plans/parked.md`, and finish the current thing first.
  - Never quietly widen scope yourself — it compounds the problem.
  - Keep answers to what was asked. If three things are in flight, say which one is
    active.
  - At the end of a work chunk, restate what's done, what's parked, and the single next
    step.
  - Prefer finishing and verifying one checkpoint over starting several.

## Don'ts

- Don't weaken or bypass `client.py`'s origin-check guard.
- Don't use a plain string type for any path-interpolated ID — always
  `SafeId`.
- Don't accept a caller-supplied full FreeAgent URL where a bare ID would do.
- Don't hand-roll OAuth token storage or refresh — `OAuthProxy` owns that.
- Don't invent FreeAgent field names, and don't trust the docs over the API.
  Confirm against the **live API** using the `request` tool in
  `scripts/freeagent_api_caller.py` (see `.claude/skills/freeagent-api/SKILL.md`) — start
  with `shape_only=true` to get the key/type outline. Order of authority:
  1. **The live API** — the only real answer.
  2. **https://dev.freeagent.com/docs** — useful, but has gaps, internal
     contradictions (`box_number` typed String while the example emits an
     integer) and outright copy-paste errors (the Delete Task section
     documents `DELETE /v2/users/:id`).
  3. **The tool inventory** — a guide. Anything in it labelled *Recommended*
     came from the docs and hasn't been checked against a real account.

  When you confirm a field this way, update the inventory and move the entry to
  *Confirmed*, saying it was verified against the live API.
- Don't add durable external storage (Object Storage, Secret Manager, Redis,
  a database) without revisiting the spec's storage decision first — the
  ephemeral-storage tradeoff was deliberate, not an oversight.
