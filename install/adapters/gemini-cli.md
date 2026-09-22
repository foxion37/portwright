# Adapter — Gemini CLI

Gemini CLI reads `~/.gemini/GEMINI.md` as its always-read instruction file.

## Install

```sh
~/developer/tools/portwright/bin/portwright client install gemini-cli
```

The Client adapter installs or updates the canonical managed block exactly once:

```text
<!-- portwright:start (managed block — safe to remove between these markers) -->
<!-- portwright:end -->
```

## Verify

```sh
~/developer/tools/portwright/bin/portwright client doctor gemini-cli
```

## Remove

```sh
~/developer/tools/portwright/bin/portwright client remove gemini-cli
```

Text outside the managed block is preserved.
