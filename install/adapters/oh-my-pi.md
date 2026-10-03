# Adapter — Oh My Pi

Oh My Pi loads `~/.omp/agent/AGENTS.md` as user-level context for every
session. The Portwright adapter adds one managed block to that file and
preserves all other text.

Oh My Pi documents this default user location in its
[Context files guide](https://github.com/can1357/oh-my-pi/blob/main/docs/context-files.md#native-omp-files).
This adapter intentionally targets the default home and does not follow a
custom `PI_CODING_AGENT_DIR` or named profile.

`$PW` is the absolute path of your clone, as set in the [install guide](../README.md) (`PW=...`); always quote it, e.g. `"$PW/bin/portwright"`. The installed text records that path, so re-run install after moving the clone. This adapter installs guidance only; it does not register an MCP server or launch the Client.

## Install

```sh
"$PW/bin/portwright" client install oh-my-pi
```

The managed block is bounded by these markers:

```text
<!-- portwright:start (managed block — safe to remove between these markers) -->
<!-- portwright:end -->
```

## Verify

```sh
"$PW/bin/portwright" client doctor oh-my-pi
```

## Remove

```sh
"$PW/bin/portwright" client remove oh-my-pi
```

Text outside the managed block is preserved.
