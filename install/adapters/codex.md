# Adapter — Codex

Codex uses the packaged `portwright-tool-use` and `portwright-tool-memory`
skills plus one always-read managed block in `~/.codex/AGENTS.md`.

`$PW` is the absolute path of your clone, as set in the [install guide](../README.md) (`PW=...`); always quote it, e.g. `"$PW/bin/portwright"`. The installed text records that path, so re-run install after moving the clone. This adapter installs guidance only; it does not register an MCP server or launch the Client.

## Install

```sh
"$PW/bin/portwright" client install codex
```

The Client adapter creates SSOT symlinks under `~/.codex/skills/` and installs
the canonical block exactly once. Re-running the command updates the managed
content without duplicating it. If `~/.codex/skills/portwright-*` already exists as something other than a symlink to this clone, install stops (exit 2) without changing anything.

The managed block is bounded by these markers:

```text
<!-- portwright:start (managed block — safe to remove between these markers) -->
<!-- portwright:end -->
```

## Verify

```sh
"$PW/bin/portwright" client doctor codex
```

## Remove

```sh
"$PW/bin/portwright" client remove codex
```

Text outside the managed block is preserved.
