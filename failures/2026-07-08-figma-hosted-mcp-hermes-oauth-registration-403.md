---
date: 2026-07-08
service: figma
service_version: "hosted mcp.figma.com/mcp + Hermes MCP client, 2026-07-08"
status: active
distributable: true
---

# Figma hosted MCP + Hermes OAuth: dynamic registration returns 403

## Symptom
Adding hosted Figma MCP to Hermes works as config, but initial OAuth cannot complete in the Hermes tool subprocess:

```bash
hermes mcp add figma --url https://mcp.figma.com/mcp --auth oauth
# non-interactive: no cached tokens
```

Forcing an interactive pseudo-tty removes the non-interactive gate but the OAuth flow then fails with:

```text
Starting OAuth flow for 'figma'...
Authentication failed: Registration failed: 403 Forbidden
```

## Verified root cause boundary
The failure is at the hosted Figma MCP OAuth client-registration step returning HTTP 403. It is not merely the local terminal being non-interactive; a pseudo-tty proceeds further and still fails at registration.

## Working fallback
Use Figma Desktop's local Dev Mode MCP instead. When Figma Desktop is running it listens on `127.0.0.1:3845` and exposes unauthenticated local tools:

```bash
lsof -nP -iTCP:3845 -sTCP:LISTEN
printf 'n\nY\n' | hermes mcp add figma_local --url http://127.0.0.1:3845/mcp
hermes mcp test figma_local
```

Verified tools discovered from `figma_local`: `get_design_context`, `get_variable_defs`, `get_screenshot`, `get_metadata`, `get_figjam`.

## Config hygiene
Do not leave hosted `figma` enabled without cached OAuth tokens; it will fail on startup/tool discovery. Keep it disabled until a Figma-compatible OAuth registration/client-id path is available:

```bash
hermes config set mcp_servers.figma.enabled false
```
