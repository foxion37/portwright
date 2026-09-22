---
date: 2026-07-08
service: figma
service_version: "hosted mcp.figma.com/mcp via Claude Code plugin (claude_ai_Figma namespace), 2026-07-08"
status: active
distributable: true
---

# Figma MCP via Claude Code: plugin server name/tool allowance mismatch

## Symptom
Claude Code reports `plugin:figma:figma: https://mcp.figma.com/mcp (HTTP) - ✓ Connected`, but a print-mode call with allowed tools like:

```bash
--allowedTools 'mcp__plugin_figma_figma__whoami,mcp__plugin_figma_figma__get_metadata'
```

still denies Figma tool calls.

## Correct root cause
In this Claude Code runtime, the hosted Figma MCP tools exposed to the agent use the actual tool namespace shown in `permission_denials` as:

```text
mcp__claude_ai_Figma__whoami
mcp__claude_ai_Figma__get_design_context
mcp__claude_ai_Figma__use_figma
```

The `plugin:figma:figma` label from `claude mcp list` is not the literal allowedTools prefix.

## Verified fix
Use the `mcp__claude_ai_Figma__...` tool names in `--allowedTools`.

Examples:

```bash
claude -p 'Use only Figma MCP. Call whoami.' \
  --model haiku --effort low \
  --allowedTools 'mcp__claude_ai_Figma__whoami' \
  --max-turns 3 --output-format json

claude -p 'Directly call use_figma once; read-only script; do not mutate.' \
  --model haiku --effort low \
  --allowedTools 'mcp__claude_ai_Figma__use_figma' \
  --disallowedTools 'Bash,Read,Write,Edit' \
  --max-turns 8 --output-format json
```

## Slides-specific notes
- `get_metadata` is design-file-only; it does not support Figma Slides (`/slides/`).
- Claude-hosted `get_design_context` also returned that Slides are unsupported in that path.
- `use_figma` DOES run against Slides and can read `editorType: "slides"`.

## Local Figma Dev Mode MCP note
Figma Desktop can expose local MCP on `127.0.0.1:3845`:

```bash
lsof -nP -iTCP:3845 -sTCP:LISTEN
curl -sS -m 3 http://127.0.0.1:3845/sse
```

With `mcporter`, local HTTP endpoints require explicit opt-in:

```bash
npx --yes mcporter list --allow-http --http-url http://127.0.0.1:3845/sse --name figma-local-sse
npx --yes mcporter list --allow-http --http-url http://127.0.0.1:3845/mcp --name figma-local-mcp
```

Without `--allow-http`, mcporter fails with `HTTP endpoints require --allow-http to confirm insecure usage.`
