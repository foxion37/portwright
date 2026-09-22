# Adapter — Claude Code

Claude Code uses two packaged skills plus a `SessionStart` hook that prints the
canonical preflight block once per session.

## Install

```sh
~/developer/tools/portwright/bin/portwright client install claude-code
```

The Client adapter creates SSOT symlinks for `portwright-tool-use` and
`portwright-tool-memory`, then merges the Portwright command into the existing
`~/.claude/settings.json` `SessionStart` hooks. It refuses malformed JSON and
does not replace existing hooks.

## Verify

```sh
~/developer/tools/portwright/bin/portwright client doctor claude-code
```

Expected state: `[OK]`. `PARTIAL` reports the missing skill or hook.

## Remove

```sh
~/developer/tools/portwright/bin/portwright client remove claude-code
```

Only Portwright-owned symlinks and the exact Portwright hook are removed.
