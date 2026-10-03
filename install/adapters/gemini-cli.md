# Adapter — Gemini CLI

Gemini CLI reads `~/.gemini/GEMINI.md` as its always-read instruction file.

`$PW` is the absolute path of your clone, as set in the [install guide](../README.md) (`PW=...`); always quote it, e.g. `"$PW/bin/portwright"`. The installed text records that path, so re-run install after moving the clone. This adapter installs guidance only; it does not register an MCP server or launch the Client.

## Install

```sh
"$PW/bin/portwright" client install gemini-cli
```

The Client adapter installs or updates the canonical managed block exactly once:

```text
<!-- portwright:start (managed block — safe to remove between these markers) -->
<!-- portwright:end -->
```

## Verify

```sh
"$PW/bin/portwright" client doctor gemini-cli
```

## Remove

```sh
"$PW/bin/portwright" client remove gemini-cli
```

Text outside the managed block is preserved.
