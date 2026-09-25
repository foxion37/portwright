# Adapter — Codex

Codex uses the packaged `portwright-tool-use` and `portwright-tool-memory`
skills plus one always-read managed block in `~/.codex/AGENTS.md`.

`<portwright>` is the directory where you cloned this repository; the installed managed block uses that path.

## Install

```sh
<portwright>/bin/portwright client install codex
```

The Client adapter creates SSOT symlinks under `~/.codex/skills/` and installs
the canonical block exactly once. Re-running the command updates the managed
content without duplicating it.

The managed block is bounded by these markers:

```text
<!-- portwright:start (managed block — safe to remove between these markers) -->
<!-- portwright:end -->
```

## Verify

```sh
<portwright>/bin/portwright client doctor codex
```

## Remove

```sh
<portwright>/bin/portwright client remove codex
```

Text outside the managed block is preserved.
