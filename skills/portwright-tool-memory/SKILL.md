---
name: portwright-tool-memory
description: Use when a new external-tool procedure or solved tool failure should be remembered in Portwright; when creating, redacting, reviewing, or promoting service/failure drafts under services/_drafts or failures/_drafts after tool setup, MCP/API/CLI troubleshooting, authentication, deployment, connector, or integration work.
---

# Portwright Tool Memory

The managed block installed by Portwright contains the executable path for
this clone. Use that path for the commands below; `<portwright>` denotes the
directory where this repository was cloned. If a separate content home is
configured, pass its `--home` flag to memory commands.

Use when: after a new external-tool procedure or solved failure should become a safe Portwright draft.

## Boundary

This skill writes or promotes memory drafts. It does not decide how to use a tool.

For preflight before using tools, use `$portwright-tool-use`.

Use this skill only after one of these is true:

- A new service connection path was derived from current docs or verified in practice.
- A tool failure was solved and the root cause is known.
- A draft in `services/_drafts/` or `failures/_drafts/` needs review or promotion.

## Draft-First Rule

Memory is draft first, not auto-final.

- New verified service procedure: draft in `services/_drafts/<id>.md`.
- New solved failure: draft in `failures/_drafts/<YYYY-MM-DD>-<service>-<slug>.md`.
- Promote to `services/` or `failures/` only after root cause is confirmed.
- Reflect an active failure lesson back into the related `services/<id>.md`.

Use the lifecycle interface instead of moving files by hand:

```bash
<portwright>/bin/portwright memory draft procedure <service-id>
<portwright>/bin/portwright memory draft lesson <service-id> <slug>
<portwright>/bin/portwright memory review <draft-path>
<portwright>/bin/portwright memory promote <draft-path>
```

`promote` validates the contract, blocks unresolved root cause or suspected
secret material, moves the draft, and links a promoted Lesson from its Procedure.

Do not store secrets. Never store tokens, cookies, OAuth codes, private keys, full raw logs, PII, or guessed root causes. Replace sensitive values with `[REDACTED]`.

## Service Draft

Create a service draft when no verified `services/<id>.md` exists and the agent has derived or verified a safe procedure.

Required destination:

`services/_drafts/<id>.md`

## Failure Draft

Create a failure draft when a real tool failure was fixed or diagnosed enough to help the next agent.

Required destination:

`failures/_drafts/<YYYY-MM-DD>-<service>-<slug>.md`

If root cause is not proven, write `unconfirmed` and do not promote.

## Write Templates

Read `references/memory-policy.md` before filling any Portwright memory draft.
