---
name: portwright-tool-use
description: Use before connecting, authenticating, instructing setup for, or calling any external tool/service; when the agent is unsure how to use a tool, sees repeated tool errors, missing credentials, stale setup docs, unknown services, API/MCP/CLI confusion, or may ask the user for manual setup.
---

# Portwright Tool Use

The managed block installed by Portwright contains the executable path for
this clone. Use that path for the commands below; `<portwright>` denotes the
directory where this repository was cloned. If a separate content home is
configured, keep the `--home` flag shown in the managed block.

Use when: before connecting, authenticating, troubleshooting, or calling any external tool.

## Interface

Before using or instructing any external tool, resolve Portwright memory first:

```bash
<portwright>/bin/portwright preflight <service-id>
```

The command resolves the Profile for the current directory (GitHub account, env
source, databases), the Procedure, matching active Lessons, a freshness state,
and an advisory tier, then returns the next action. Add `--json` for
machine-readable output. Honour `confirm` and `forbid` through your own
permission prompt; a version-match `fresh` proves the versions agree, not that
every step is still correct.

## Choosing a model for the work

Before delegating or escalating, read the routing table once per session:

```bash
<portwright>/bin/portwright models
```

It maps task classes to a neutral capability tier and effort (`modelchk` scale)
and says which questions belong to the decision model (JEV) or plain code
instead of a language model. Rules that always hold: closed questions never go
to a language model first; review runs on a different model than the author;
language models receive the selected note and evidence only, never the whole
cache. The harness binds tiers to concrete models; do not hard-code vendors.

To load the version-matched guides without copying files:

```bash
<portwright>/bin/portwright skills get portwright-tool-use
<portwright>/bin/portwright skills get portwright-tool-memory
```

## User Burden

Do not ask the user to create tokens, paste secrets, or manually configure dashboards when the agent can do it through MCP, CLI, API, or an existing connector.

Ask only for genuinely human-only steps:

- OAuth consent click
- account login / identity proof
- ToS or permission approval

If a human step is needed, run `<portwright>/bin/portwright preflight <service-id> --intent instruct`,
then derive the current path from official docs before instructing the User.

## Unknown Or Broken Tool

If no `services/<id>.md` exists:

1. Treat `DERIVE REQUIRED` as the normal derive-on-miss state.
2. Derive from current official docs before instructing the User.
3. Create a Procedure draft with `<portwright>/bin/portwright memory draft procedure <service-id>`.

If the tool is failing:

1. Stop trial-and-error.
2. Run `<portwright>/bin/portwright preflight <service-id> --intent recover`.
3. Try the returned cached correction.
4. After a confirmed new fix, create a Lesson draft with
   `<portwright>/bin/portwright memory draft lesson <service-id> <slug>`.

## Boundary

This skill must read existing memory before acting. It applies existing memory and must not write durable memory. Do not write durable memory from this skill.

For Procedure or Lesson draft review and promotion, use `$portwright-tool-memory`.

Do not treat the repo's future governance files as active v1 enforcement. Portwright v1 is a read-before-acting knowledge layer, not a policy gateway.
