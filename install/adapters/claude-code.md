# Adapter — Claude Code

Claude Code uses two packaged skills plus a `SessionStart` hook that prints the
canonical preflight block once per session.

`$PW` is the absolute path of your clone, as set in the [install guide](../README.md) (`PW=...`); always quote it, e.g. `"$PW/bin/portwright"`. The installed text records that path, so re-run install after moving the clone. This adapter installs guidance only; it does not register an MCP server or launch the Client.

## Install

```sh
"$PW/bin/portwright" client install claude-code
```

The Client adapter creates SSOT symlinks for `portwright-tool-use` and
`portwright-tool-memory`, then merges the Portwright command into the existing
`~/.claude/settings.json` `SessionStart` hooks. It refuses malformed JSON and
does not replace existing hooks. If `~/.claude/skills/portwright-tool-use` or `portwright-tool-memory` already exists and is not a symlink to this clone, install stops (exit 2) without changing anything; move that path aside first.

## Verify

```sh
"$PW/bin/portwright" client doctor claude-code
```

Expected state: `[OK]` and exit 0. `PARTIAL` reports the missing skill or hook.

## Remove

```sh
"$PW/bin/portwright" client remove claude-code
```

Only Portwright-owned symlinks and the exact Portwright hook are removed.
