# freeagent-mcp-remote

This "connector" was built to give Claude access to my
[FreeAgent](https://www.freeagent.com/) data. At this point it is an internal tool, however
I tried to write clearly and for the general public in case it would be useful for others.
If you need help to set up, adapt, or if you'd like a similar tool for your business,
[ask a question](../../discussions).

Inspired by [samaxbytez/freeagent-mcp](https://github.com/samaxbytez/freeagent-mcp) Although initially I thought I'd be developing on top of it I decided to start from scratch using [https://gofastmcp.com] and Python.

- **WIP** — "work in progress". A term used by developers, used throughout this readme to
  mark functionality that is not yet available. Equivalent to "coming soon".

## What's available

|                                                                               | Status        |
| ----------------------------------------------------------------------------- | ------------- |
| **A FreeAgent command-line tool** — read your accounting data from a terminal | **Works now** |
| **A Claude connector** — ask Claude questions about your books                | **WIP**       |

They share the same setup, so following the steps below gets you the working half today.

## How it works

This project is a small server that sits between FreeAgent and Claude (or another AI
provider) and acts as a translator. Once it's connected, you can ask Claude things like
_"which bank transactions from March are still unexplained?"_ and Claude can go and look.

The technical name for this kind of translator is an **MCP server** — MCP being a shared
standard for connecting AI assistants to outside tools. In Claude, these show up as
**connectors**. You don't need to know anything more than that to use it.

Additional reading:

[What is MCP?](https://modelcontextprotocol.io/docs/2026-07-28/getting-started/intro)
explains it in plain terms (think "a USB-C port for AI").

[Get started with custom connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp).

---

# Setup

Needed for both the command-line tool and (later) the connector. Written assuming you can
follow a terminal, but haven't necessarily built a Python service before.

## 1. Get FreeAgent credentials

Before connecting to FreeAgent you need to register an "app". That gives you two strings —
a **client ID** and a **client secret** — which together identify this server to FreeAgent.
This way one "app" can be installed on different FreeAgent organisations, and for example
if an "app" is found to be malicious FreeAgent can uninstall it from all the organisations
at once. Unfortunately this registration is required even if you only want to connect to
your own account.

1. Go to the [FreeAgent Developer Dashboard](https://dev.freeagent.com/) and sign in.
2. Create an app.
3. Set the **OAuth redirect URI** to `http://localhost:8723/callback`. This is where
   FreeAgent sends your browser back to after you approve access, so it has to match
   exactly — a trailing slash will break it.

   That address is the one the command-line tool uses, because the browser comes back to
   your own machine. The connector, once it's deployed, is reached at a public web address
   instead, and so needs its own redirect URI registered —
   `<the container's URL>/auth/callback`. Nothing to do about that now; the
   [deployment runbook](docs/plans/freeagent-mcp-remote-deployment.md) covers it at the
   point where it matters. It's only worth knowing so that seeing two different addresses
   later doesn't look like one of them is a mistake.

4. Duplicate `.env.example` into `.env` if you haven't already. That file is not checked
   into git, thanks to [`.gitignore`](.gitignore). Copy the OAuth identifier and secret
   into it as `FREEAGENT_CLIENT_ID` and `FREEAGENT_CLIENT_SECRET`.

## 2. Install

The only thing you need installed first is [uv](https://docs.astral.sh/uv/), a tool that
manages Python projects. It fetches the right version of Python for you, so you don't need
Python installed already and don't need to know anything about virtual environments.

On a Mac, with [Homebrew](https://brew.sh/):

```bash
brew install uv
uv --version    # check it worked
```

Other platforms, and other ways to install it, are covered in
[uv's installation guide](https://docs.astral.sh/uv/getting-started/installation/).

Then:

```bash
git clone <this-repo> && cd freeagent-mcp-remote
uv sync                 # creates .venv, installs everything, fetches Python 3.14
cp .env.example .env    # then add the credentials from the step above
```

`uv sync` takes a minute the first time and is near-instant afterwards.

`uv run <command>` runs things inside that environment, which is why every command below
starts with it.

## 3. Authorise

```bash
uv run scripts/fa_auth.py
```

A browser opens, you approve access, and it writes a token back to `.env`. FreeAgent's
access tokens last an hour, but a refresh token is saved alongside and used automatically,
so this is genuinely a one-time step.

> **Sandbox.** FreeAgent offers a free sandbox at
> [signup.sandbox.freeagent.com](https://signup.sandbox.freeagent.com/signup) — a throwaway
> company you can safely write to. It needs its own signup and its own app registration;
> sandbox credentials don't work against production. Point at it by setting
> `FREEAGENT_API_BASE_URL=https://api.sandbox.freeagent.com/v2`, and the login endpoints
> follow automatically so the two can't get crossed. Worth doing before anything that
> writes; not worth it for reading, since a sandbox has none of your actual data.

---

# Using the command-line tool

This works today. It reads any part of your FreeAgent account from the terminal, handling
the login for you.

FreeAgent's data is organised into "endpoints" — `/company`, `/invoices`,
`/bank_accounts` and so on. The [FreeAgent API docs](https://dev.freeagent.com/docs/) list
them all. You ask for one like this:

```bash
uv run fastmcp call scripts/freeagent_api_caller.py request path=/company
```

### Some things to try

All of these are read-only and safe.

```bash
# Your company profile: year end dates, VAT registration, company type
uv run fastmcp call scripts/freeagent_api_caller.py request path=/company

# Bank accounts, including how many transactions are still unexplained
uv run fastmcp call scripts/freeagent_api_caller.py request path=/bank_accounts

# Trial balance — every nominal account and its total
uv run fastmcp call scripts/freeagent_api_caller.py request \
    path=/accounting/trial_balance/summary

# One contact, to see what fields a contact has
uv run fastmcp call scripts/freeagent_api_caller.py request \
    --input-json '{"path": "/contacts", "params": {"per_page": "1"}}'

# Click around in a browser instead
uv run fastmcp dev inspector scripts/freeagent_api_caller.py
```

Simple arguments go as `key=value`. Nested ones — `params` and `body` — need
`--input-json`, which can carry the whole call.

### Keeping the output manageable

A list endpoint can return thousands of records. Two ways to trim it, and they combine:

- **`per_page=1`** limits how many _records_ come back. Usually what you want — one real
  record shows you the actual formats values come in.
- **`shape_only=true`** field names and types, no values. Useful for learning API shape during development.

```bash
uv run fastmcp call scripts/freeagent_api_caller.py request path=/invoices shape_only=true
```

### Other options

| Argument        | What it does                                                         |
| --------------- | -------------------------------------------------------------------- |
| `path`          | Which endpoint to call. The only required one.                       |
| `method`        | `GET` by default.                                                    |
| `params`        | Query options, e.g. `{"view": "unexplained"}`. Needs `--input-json`. |
| `show_headers`  | Adds the true record count and paging links to the result.           |
| `confirm_write` | Required before anything that changes data.                          |

Changing data (`POST`, `PUT`, `DELETE`) needs `confirm_write=true`. That's deliberate
friction — these are your real accounting records. Use the sandbox for those.

---

# [WIP] The Claude connector

Not ready yet. When it is, you'll be able to add this to Claude as a connector and ask
questions in plain language rather than calling endpoints yourself:

- Work through unexplained bank transactions and suggest how to categorise them
- Pull up the profit & loss, balance sheet or trial balance for a period
- Look at journal entries, or post corrections
- Prepare figures for VAT returns and corporation tax
- Review payroll and PAYE figures
- Think through the salary-versus-dividends split using your actual profit
- Track time, tasks and projects

The difference from the command-line tool is that the connector exposes each of these as a
separate, narrow capability rather than one general "call anything" command — for reasons
under [Safety](#safety) below.

---

# For developers

## Everyday commands

```bash
uv run pytest                  # run the tests
uv run pytest --lf             # just the ones that failed last time
uv run ruff format .           # auto-format the code
uv run ruff check .            # find likely mistakes and style problems
uv run mypy                    # check the types line up
```

`mypy` is the one worth not skipping: it's set to strict, so it catches a whole class of
"this could be nothing here" bugs before they ever run.

## Checks on commit

A [git hook](https://git-scm.com/book/en/v2/Customizing-Git-Git-Hooks) runs all four
automatically every time you commit. Enable it once:

```bash
git config core.hooksPath .githooks
```

The whole suite takes about two seconds. If something fails, the commit stops and you get
the output.

**To commit anyway**, use git's built-in bypass:

```bash
git commit --no-verify -m "..."
```

The hook also refuses outright to commit `.env`, which holds a live FreeAgent secret and
access token.

## Working on the connector

```bash
# What tools does the server expose, and what do their inputs look like?
uv run fastmcp inspect src/server.py:create_server

# Click through it in a browser
uv run fastmcp dev inspector src/server.py:create_server
```

Note the `:create_server` at the end — these commands need the file _and_ the name of the
function inside it that builds the server, not just the filename.

**FreeAgent's documentation has gaps, contradictions and at least two copy-paste errors**,
so the connector's tools are designed against real API responses rather than against the
docs. That's what the command-line tool above is for. `scripts/freeagent_api_caller.py` is local
only and must never be deployed; there's a test that fails if it ever reaches the deployed
server.

# Learning

## Caches

Three directories appear once you've run the tools. All are generated, gitignored, and
never inputs to the program — deleting any of them costs nothing but a slower next run.

- **`.mypy_cache/`** — what mypy learned about each file's types, so re-checking an
  unchanged file is a cache read rather than a fresh analysis. The one that matters most:
  without it, every run re-analyses all your dependencies' type information.
- **`.pytest_cache/`** — which tests failed last time. This is what powers `pytest --lf`
  (last-failed) and `--ff` (failed-first), so you can iterate on just the broken tests.
- **`.ruff_cache/`** — per-file lint results. Ruff is fast enough that you'd barely notice
  this one missing.

If anything ever behaves strangely, `rm -rf .mypy_cache .pytest_cache .ruff_cache` is a
safe reset.

---

# If you get stuck

I built this for my own company's books, and wrote it up properly in case it's useful to
someone else.

If you're trying to set up something like this and it isn't going well, I do this kind of
work professionally and I'm happy to talk. <!-- TODO: name + how to get in touch -->

If you've found a bug or something here is wrong, an issue is welcome.
