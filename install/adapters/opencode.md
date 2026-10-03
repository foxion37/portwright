# Adapter — OpenCode

OpenCode loads `~/.config/opencode/AGENTS.md` in every session. The Portwright
adapter adds one managed block to that file and preserves all other text.

OpenCode documents this global rule location in its
[Rules guide](https://opencode.ai/docs/rules/#global).

`$PW` is the absolute path of your clone, as set in the [install guide](../README.md) (`PW=...`); always quote it, e.g. `"$PW/bin/portwright"`. The installed text records that path, so re-run install after moving the clone. This adapter installs guidance only; it does not register an MCP server or launch the Client.

## Install

```sh
"$PW/bin/portwright" client install opencode
```

The managed block is bounded by these markers:

```text
<!-- portwright:start (managed block — safe to remove between these markers) -->
<!-- portwright:end -->
```

## Verify

```sh
"$PW/bin/portwright" client doctor opencode
```

## Remove

```sh
"$PW/bin/portwright" client remove opencode
```

Text outside the managed block is preserved.
