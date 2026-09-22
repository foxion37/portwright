# Adapter — OpenCode

OpenCode loads `~/.config/opencode/AGENTS.md` in every session. The Portwright
adapter adds one managed block to that file and preserves all other text.

OpenCode documents this global rule location in its
[Rules guide](https://opencode.ai/docs/rules/#global).

## Install

```sh
~/developer/tools/portwright/bin/portwright client install opencode
```

The managed block is bounded by these markers:

```text
<!-- portwright:start (managed block — safe to remove between these markers) -->
<!-- portwright:end -->
```

## Verify

```sh
~/developer/tools/portwright/bin/portwright client doctor opencode
```

## Remove

```sh
~/developer/tools/portwright/bin/portwright client remove opencode
```

Text outside the managed block is preserved.
