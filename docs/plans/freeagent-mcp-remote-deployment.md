# freeagent-mcp-remote — Scaleway Deployment Runbook

> Operational steps for deploying `freeagent-mcp-remote` to Scaleway and connecting it to an MCP client (Claude, or any other). Distinct from the [main build plan](./freeagent-mcp-remote.md) — this is what you run once the code exists and is containerized, and again on subsequent deploys.
>
> `<ANGLE_BRACKET>` values are placeholders — fill in with your own Scaleway account/project details as you go. Region used throughout: `fr-par` (adjust if you use a different Scaleway region).
>
> Storage note: `OAuthProxy` uses its own default local file store (`client_storage` left unset) rather than a durable external backend — see the [main spec's storage decision](./freeagent-mcp-remote.md#key-decision-oauthproxy-as-the-authorization-layer) for why. That means no Object Storage bucket, no Secret Manager, no fixed encryption keys to provision here — but it also means the container's OAuth state is wiped on every cold start (accepted tradeoff for a single-tenant personal tool).

## Prerequisites

- `freeagent-mcp-remote` builds and its Docker image runs locally (main spec, [task 5](./freeagent-mcp-remote.md#5-dockerfile-and-container-build)). Note: the container needs `FREEAGENT_CLIENT_ID`, `FREEAGENT_CLIENT_SECRET` and `PUBLIC_BASE_URL` set to start — it fails fast at boot if any is missing (`auth.py::_required_env`). Dummy values suffice for a local `/health` check; the real values get set on the deployed container in [step 5](#5-set-the-containers-environment-variables-and-secrets).
- Scaleway CLI (`scw`) installed and authenticated
- A FreeAgent OAuth app registered at https://dev.freeagent.com/ with a client ID/secret

## Steps

### 1. Create the Container Registry namespace
```bash
scw registry namespace create name=freeagent-mcp-remote region=fr-par
```
Note the returned namespace ID and endpoint.

### 2. Build and push the image
```bash
docker build -t rg.fr-par.scw.cloud/freeagent-mcp-remote/server:latest .
docker login rg.fr-par.scw.cloud -u nologin -p <SCW_SECRET_KEY>
docker push rg.fr-par.scw.cloud/freeagent-mcp-remote/server:latest
```

### 3. Create the Container namespace and container
```bash
scw container namespace create name=freeagent-mcp-remote region=fr-par
# note the returned namespace ID as <NAMESPACE_ID>

scw container container create \
  name=freeagent-mcp-remote \
  namespace-id=<NAMESPACE_ID> \
  registry-image=rg.fr-par.scw.cloud/freeagent-mcp-remote/server:latest \
  port=8080 \
  min-scale=0 \
  max-scale=1 \
  memory-limit=256 \
  cpu-limit=140 \
  privacy=public \
  region=fr-par
```
`min-scale=0` lets the container scale to zero between uses (no cost while idle — this is a personal tool, not something under constant load; it's also *why* OAuth state resets on cold start, per the storage note above). `max-scale=1` because this is single-tenant; there's never a need for more than one instance serving traffic at once.

Note the container's returned public URL, and confirm its exact domain pattern via `scw container container get` — this user's prior Scaleway deployment (`kivi.dashboard`) was a Function, not a Container, and domain patterns can differ between the two.

### 4. Register the FreeAgent OAuth app's redirect URI
In the FreeAgent Developer Dashboard (https://dev.freeagent.com/), set the app's OAuth redirect URI to `<CONTAINER_URL>/auth/callback` — `OAuthProxy`'s default `redirect_path`. **This is the only manual, out-of-band step this project's OAuth design requires** (FreeAgent doesn't speak DCR/CIMD, so someone has to register the app and its redirect URI by hand, once). **Confirmed:** `src/auth.py` leaves `redirect_path` unset, and the running server's route table was verified to contain `/auth/callback`, so that is the path to register.

### 5. Set the container's environment variables and secrets
```bash
scw container container update <CONTAINER_ID> region=fr-par \
  environment-variables.PORT=8080 \
  environment-variables.PUBLIC_BASE_URL=<CONTAINER_URL> \
  secret-environment-variables.FREEAGENT_CLIENT_ID=<FREEAGENT_CLIENT_ID> \
  secret-environment-variables.FREEAGENT_CLIENT_SECRET=<FREEAGENT_CLIENT_SECRET>
```
These names are read directly by `src/auth.py` and `src/server.py`; they are not free choices. `PUBLIC_BASE_URL` is required — the container exits at startup without it.

**Do not set `FREEAGENT_DEV_TOKEN`.** It is a local-development escape hatch that bypasses `OAuthProxy`'s token lookup entirely (see `.claude/skills/freeagent-api/SKILL.md`). In a deployed container it would be a standing credential leak.

`FREEAGENT_API_BASE_URL` is optional and defaults to `https://api.freeagent.com/v2`. Set it only to target the sandbox — the OAuth authorization and token endpoints are derived from it, so the OAuth flow and the API client can never point at different environments.

That's the full set — no Scaleway storage credentials, no signing/encryption keys. `FREEAGENT_CLIENT_SECRET` doubles as the seed for `OAuthProxy`'s default HKDF-derived JWT signing key (see the main spec), so nothing extra needs generating for that either.

### 6. Deploy
```bash
scw container container deploy <CONTAINER_ID> region=fr-par
```

### 7. Add the connector in an MCP client
In Claude.ai: Settings → Connectors → Add custom connector → enter `<CONTAINER_URL>/mcp` as the URL. Because this server is a standards-compliant OAuth MCP server, the client detects this via standard OAuth discovery (confirmed against Anthropic's docs — no manual "select auth type" step) and drives you through a normal browser consent flow — which, behind the scenes, terminates in FreeAgent's own real consent screen (approve access to your FreeAgent account) before redirecting back and completing. **There is no bearer token to copy-paste, and no separate script to run first** — this connector-add step *is* the one-time authorization, for whichever MCP client does it first (and for any other client added later, it repeats independently).

Expect to repeat this step after any period of inactivity long enough for the container to have scaled to zero and cold-started again — see the storage note at the top of this doc. Exact field layout depends on Claude.ai's current connector UI — verify at implementation time, since UI details change independently of this spec.

### 8. Write up this deployment's actuals
Capture steps 1–7 with this deployment's actual namespace IDs/URLs filled in as a project-local `docs/deploy.md` — that's the copy future-you edits when redeploying, this runbook is the template.

## Re-deploying after a code change

Repeat steps 2 and 6 only (build/push the new image, redeploy the container) — steps 1, 3, 4, 5 are one-time setup and don't need to be repeated unless the corresponding infrastructure changes. A redeploy also resets `OAuthProxy`'s state the same way a cold start does (new container instance, same ephemeral local store) — expect to reconnect afterward, same as after any cold start.

## Verification after deploying

See [the main spec's end-to-end verification task](./freeagent-mcp-remote.md#7-end-to-end-verification) for the full checklist: local Docker smoke test, live checks against `<CONTAINER_URL>`, a live check through an MCP client's full OAuth consent flow, and the cold-start behavior check specific to this design.
