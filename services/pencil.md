---
id: pencil
display_name: Pencil (Pen.app MCP)
version_tag: "Pen.app bundled mcp-server-darwin-arm64 (--agent claudeCodeCLI)"
last_verified: "2026-07-25"
endpoint:
  type: mcp
  server: "bundled in /Applications/Pen.app (stdio), tools mcp__pencil__*"
human_steps:
  - "Keep Pen.app installed locally — no macOS Accessibility/Screen Recording grants are needed for the MCP path."
agent_can:
  - "Launch the app itself (open -a Pen, wait ~5s) when the transport is not connected."
  - "Create/access .pen files directly via batch_design with an absolute filePath — no UI interaction needed."
status: active
distributable: true
---

# Pencil (pen.dev Pen.app MCP)

- MCP server: bundled inside `/Applications/Pen.app` (`mcp-server-darwin-arm64 --app desktop --agent claudeCodeCLI`), tools `mcp__pencil__*`.
- .pen files are encrypted — never Read/Grep them; MCP tools only.

## Verified setup (2026-07-25)

1. **App must be running**: `transport not connected to app: desktop` → `open -a Pen` (app name is **Pen**, not Pencil), wait ~5s.
2. **"A file needs to be open in the editor"**: get_editor_state/batch_get need *some* file open in the editor. BUT `batch_design` with an **absolute `filePath`** creates/accesses the file directly even if it isn't open in the editor — no UI interaction needed:
   ```
   batch_design(filePath="/abs/path/new-file.pen", input='SetVariables({...})')  # creates the file
   ```
   All subsequent pencil tools then work by passing the same `filePath`.
3. Driving the app UI (Cmd+N via osascript/computer-use) is NOT needed and is blocked without macOS Accessibility/Screen Recording grants anyway.

## Gotchas

- **Fonts**: only Google fonts + editor-bundled fonts. `Pretendard`/`Pretendard Variable` are invalid → use `Noto Sans KR` in .pen files (keep Pretendard for the actual code implementation).
- **Icons**: lucide set is incomplete/renamed (`message-circle-question`, `circle-help`, `help-circle` all missing). `feather`'s `help-circle` works. Warnings appear in batch_design result — fix in next call.
- Sizing warnings ("Collapsed size … circular fill_container/fit_content") are actionable and accurate; fix by giving the screen frame a fixed height after content is built (measure with snapshot_layout).
