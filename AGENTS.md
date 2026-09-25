# AGENTS.md — working on Portwright

Read this before changing anything in this repository. It applies to AI agents and to people; humans can start at [CONTRIBUTING.md](CONTRIBUTING.md).

Korean copy: [AGENTS_KR.md](AGENTS_KR.md). Edits here must be mirrored there in the same change.

## What this repository is

Portwright makes an agent read a short note about a tool **before** it touches that tool. It does not execute tools, hold credentials, or sit in the call path.

- `services/<tool>.md` — the Procedure that is correct today.
- `failures/<date>-<tool>-<slug>.md` — a Lesson with a confirmed root cause.
- `profiles/<project>.md` — which account, secret source, and database a directory belongs to. Personal by nature; none ship here.
- `lib/`, `bin/portwright`, `tests/` — the mechanism that reads, validates, and routes those notes.

It is not a gateway, a policy engine, a background server, an audit pipeline, or a pre-filled registry of every tool on the internet.

## This tree is generated

This repository is a one-way export from a private workstation repository. **Commits made directly here are overwritten by the next export.**

That is deliberate: the source repository also holds personal notes that must never be published, so publication is a filtered build rather than a mirror. See [Roles and scope](docs/roles-and-scope.md) and [CONTRIBUTING.md](CONTRIBUTING.md). Until the planned Hub MCP submission path arrives in M2, this public tree does not accept lesson contributions; never submit lesson text through GitHub Issue/PR free-form bodies.

## Two absolute rules

1. **No secret value belongs in any file.** Not in notes, code, examples, tests, or commit messages. Reference names only (`GITHUB_TOKEN`, a 1Password item name). Replace anything sensitive with `[REDACTED]`.
2. **`human_steps` lists only what a human genuinely must do.** "Create a token and paste it here" when a CLI, MCP server, or existing connection could act is the exact anti-pattern this project exists to remove. Every avoidable human step is a defect.

## The note contract

Start from `services/_TEMPLATE.md` or `failures/_TEMPLATE.md`; they define the required frontmatter.

- Procedure keys: `id`, `display_name`, `version_tag`, `last_verified`, `endpoint{type,server}`, `human_steps[]`, `agent_can[]`, `status`, `distributable`. Optional: `profile_ids`, `freshness_evidence.url`.
- Lesson keys: `date`, `service`, `service_version`, `status`. Filename: `YYYY-MM-DD-<service>-<slug>.md`.
- `version_tag` must be a string the next agent can reproduce from current docs (`clasp 3.3.0`), because freshness compares it verbatim.
- `distributable: false` means the note stays on its workstation in a gitignored `_private/` root. Those notes never reach this repository.
- `bin/portwright check` must pass. It validates every note, including drafts.

## Memory is draft-first

Never move note files by hand. Use the lifecycle:

```sh
bin/portwright memory draft procedure <service-id>
bin/portwright memory draft lesson <service-id> <slug>
bin/portwright memory review <draft-path>
bin/portwright memory promote <draft-path>
```

`review` screens for suspected secret material; `promote` validates the contract, refuses an unconfirmed root cause, and links a promoted Lesson from its Procedure. If the root cause is guessed, write `unconfirmed` and do not promote.

## Derive on miss; never hand-author a library

Procedures are cached because they were needed, not written in advance. Do not pre-write procedures for unused tools.

When a note is missing or stale, derive the current procedure from the tool's official documentation, then cache it. When official docs and the shipped tool disagree, trust what you verified against the tool and say so in the note. Never hand a user a setup step you did not check against the current path.

## Semantics you must not soften

- `unknown` is never `fresh`. Missing evidence yields `unknown` plus verify-required.
- `fresh` from a version match claims only that the versions agree — not that every step was re-proved. Keep that wording honest in code and docs.
- The tier (`auto|confirm|forbid`) is **advice**. Clients enforce it through their own permission prompts. Adding enforcement, interception, or a gateway contradicts decision D1 in `ARCHITECTURE.md` and will be rejected.
- Unmatched or unknown input falls back to `confirm`. Fail toward asking.

## Constraints

- Python standard library only. No runtime dependencies, no build system, no daemon.
- English docs and their `_KR` twins change together in one commit.
- Keep behavior deterministic and offline-testable; model judgments are optional support, never the only path to an answer.

## Verify before you claim anything works

```sh
python3 -m unittest discover -s tests -q
bin/portwright check
```

Both must pass. A test earns its place only if a plausible bug would fail it.

## What will be rejected

Personal notes, profiles, machine-specific paths, account handles, organization names, credentials in any form, new dependencies, gateway or enforcement features, and pre-written procedure libraries for unused tools.
