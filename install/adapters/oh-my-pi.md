# Adapter — Oh My Pi

Oh My Pi loads `~/.omp/agent/AGENTS.md` as user-level context for every
session. The Portwright adapter adds one managed block to that file and
preserves all other text.

Oh My Pi documents this default user location in its
[Context files guide](https://github.com/can1357/oh-my-pi/blob/main/docs/context-files.md#native-omp-files).
This adapter intentionally targets the default home and does not follow a
custom `PI_CODING_AGENT_DIR` or named profile.

`<portwright>` is the directory where you cloned this repository; the installed managed block uses that path.

## Install

```sh
<portwright>/bin/portwright client install oh-my-pi
```

The managed block is bounded by these markers:

```text
<!-- portwright:start (managed block — safe to remove between these markers) -->
<!-- portwright:end -->
```

## Verify

```sh
<portwright>/bin/portwright client doctor oh-my-pi
```

## Remove

```sh
<portwright>/bin/portwright client remove oh-my-pi
```

Text outside the managed block is preserved.
