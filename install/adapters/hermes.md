# Adapter — Hermes

Hermes reads `~/.hermes/SOUL.md` as its always-read instruction file.

## Install

```sh
~/developer/tools/portwright/bin/portwright client install hermes
```

The Client adapter installs or updates the canonical managed block exactly once:

```text
<!-- portwright:start (managed block — safe to remove between these markers) -->
<!-- portwright:end -->
```

## Verify

```sh
~/developer/tools/portwright/bin/portwright client doctor hermes
```

## Remove

```sh
~/developer/tools/portwright/bin/portwright client remove hermes
```

Text outside the managed block is preserved.
