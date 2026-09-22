---
date: "2026-06-03"
service: codex-cli
service_version: "codex CLI (2026-06-03)"
status: active
distributable: true
---

# Codex CLI exec approval flag position

## Symptom

`codex exec ... -a never ...` failed with `error: unexpected argument '-a' found`.

## 진짜 원인 (root cause)

In this Codex CLI version, `-a/--ask-for-approval` is a global flag and must be
passed before the `exec` subcommand.

## Fix (verified)

Use `codex -a never exec ...`, not `codex exec ... -a never ...`.

Verified command shape:

```bash
codex -a never exec --ignore-user-config --ephemeral -C /path -s workspace-write -o output.md - < prompt.md
```
