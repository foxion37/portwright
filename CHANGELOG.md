# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.1.0] - 2026-09-25

### Added
- `bin/portwright review`: improvement proposals counted from the note corpus and a local usage ledger (`_local/usage.jsonl`, one metadata line per preflight — service, profile, freshness, tier, intent, procedure path; never arguments, results, paths, or credentials). Findings cover repeated recent failures, repeated cache misses, repeated `unknown` freshness without declared evidence, stale-but-used notes, and lessons a procedure never references. JEV is optional and may only reorder. Reports land in `_local/reviews/<date>.md`; `_local/` is gitignored and never exported.
- `scripts/export_public.py` and `install/public-export.json`: one-way public distribution export. Selection is mechanical (include prefixes/files minus excludes, `distributable: true` notes only), personal identifiers are substituted and then gated by regex markers, and any surviving marker aborts the run before writing a file. `tests/test_public_export.py` covers selection, the marker gate, and dry-run isolation.
- `LICENSE` (MIT) and a `Credits And Prior Art` section in both READMEs naming every borrowed repository and what was taken from it, plus an `In Plain Words` introduction covering the three note kinds, one preflight call, and the freshness/tier/self-filling-cache promises.
- `public/AGENTS.md`, `public/AGENTS_KR.md`, and `public/CONTRIBUTING.md`: the public repository's own agent and contributor guide, exported to the root. The workstation `AGENTS.md` stays private, so the export config gained a `renames` map.
- `services/google-apps-script.md`: clasp 3.3.0 procedure, derived because the official Apps Script guide still documents 2.x command names.
- One note catalog reads `_private`, tracked, then `_hub` sources; duplicate ids within a source fail checks, while cross-source overrides warn.
- One distributable-note rule governs both public exports and skill bundles.
- `install/identifier-policy.json` and `install/secret-patterns.json` centralize publication identifiers and secret screening.
- `docs/roles-and-scope.md` and its Korean companion distinguish local users from the planned public and company Hubs.
- An outside-user acceptance test installs Claude Code and Codex from a public export cloned at an arbitrary path with an empty HOME.

### Changed
- The CLI and MCP preflight surfaces share a decision path and record usage in the local ledger.
- Packaged resources and content-home roots resolve independently; local installations no longer depend on the author's checkout.
- The public export excludes operator-only scripts; the planned Hub MCP will accept contributions in M2 instead of GitHub Issue/PR free-text submissions.

### Safety
- Identifier and secret screening reject disallowed content before export or publication; `_private` notes and user profiles remain local.

## [2.0.0] - 2026-09-21

### Added
- `profiles/<id>.md` Profile files (project path, GitHub account, env source reference, database references, services, default tier, host) and profile-aware `portwright preflight` that resolves the Profile for the current directory before returning the Procedure.
- Deterministic freshness decision table in preflight output (`fresh|stale|unknown` plus `none|derive-required|verify-required`), with TypeSafe JEV Noul as supporting evidence only.
- Advisory approval tier (`auto|confirm|forbid`) with rationale in preflight output; explicit forbid rules beat matched rules, Profile defaults, and JEV Score, and unmatched or unknown judgments default to `confirm`.
- `portwright browser select` picks one serialized browser candidate via JEV Choice and refuses on no candidates, confidence below 0.6, or a top-2 margin below 0.15.
- `portwright update [--dry-run] [--json]` performs a guarded Git fast-forward, local stale-note schema checks, and explicit official-document retrieval for public Procedures with `freshness_evidence.url`. `docs_fetched`, `docs_changed`, `docs_failed`, and `docs_unconfigured` distinguish retrieval from the local `revalidated` count. Dry-run never fetches document bodies.
- Private note roots `services/_private/` and `failures/_private/` (gitignored) for `distributable: false` notes, loaded by the same catalog.
- `distributable` frontmatter field on Procedures and Lessons, plus optional `profile_ids` / `profile_id` bindings.
- JEV client (`lib/portwright/jev.py`) for TypeSafe Jev Choice/Score/Noul judgments, with deterministic fixture replay under `tests/fixtures/jev/` via `PORTWRIGHT_JEV_FIXTURES`.
- Official document evidence is cached under ignored `services/_private/_evidence/` and consumed by preflight when no explicit evidence flags are given. A download never auto-verifies or rewrites a Procedure; source URL, SHA-256 and fetch date remain visible.
- Managed Client adapters for OpenCode, Oh My Pi, and VS Code Copilot, selected from Clients with local use evidence.
- Temporary-HOME coverage for install, reinstall, removal, user-text preservation, and VS Code always-apply frontmatter.
- Verified Procedure caches for OpenRouter, xAI Grok API, DeepSeek API, and OpenAI API. These are docs-derived; no credential creation or paid request was performed.

### Changed
- `portwright preflight` output now includes `profile`, `freshness`, `tier`, and `rationale` fields alongside the existing Procedure and Lessons.
- `check` now covers both the tracked note roots and the `_private` roots.

### Safety
- The approval tier is advisory only; there is still no gateway, and Clients enforce `confirm`/`forbid` through their own permission prompts.
- The JEV API key is injected at runtime via `opsvc` from 1Password and is never stored; missing credentials yield a secret-safe `unknown` judgment.
- `unknown` freshness is never reported as `fresh`; JEV Noul can strengthen a stale rationale but never flips a deterministic result.
- Private promotion and Lesson linking reject symlinked directory aliases; a failed link preserves the draft and leaves public Procedures unchanged.
- Unscoped GitHub calls require confirmation because the service name alone does not distinguish reads from deletion, publishing, or permission changes.
- The VS Code adapter uses a dedicated profile instruction file and refuses an existing file that lacks the required frontmatter.
- Live Client configuration remains unchanged until the user explicitly runs a `client install` command.

## [1.2.0] - 2026-07-13

### Added
- `portwright preflight <service-id>` resolves a Procedure and active/stale Lessons by frontmatter before Agent action, recovery, or human instruction.
- Python stdlib deep modules for note contracts, Memory lifecycle, and five Client adapters behind the stable `bin/portwright` interface.
- `portwright memory draft|review|promote` with draft idempotency, promotion guards, secret detection, and automatic Lesson links in related Procedures.
- `portwright client install|remove|doctor` with idempotent managed blocks, SSOT skill symlinks, backups, Claude hook merging, and an explicit Cursor manual state.
- Interface-level tests for nested contract validation, derive-on-miss, current-doc instruction gating, draft promotion, and Client installation.

### Changed
- `bin/portwright` is now a thin executable adapter; `lint` and `doctor` remain compatibility commands.
- JSON schema is the contract source for required fields and enums; `check` additionally validates dates, list shapes, filenames, root cause sections, and drafts.
- The canonical reminder block now routes every Client through the packaged preflight and Memory interfaces instead of duplicating write rules.
- Client adapter docs use the shared installer rather than non-idempotent `cat >>` or copied skill directories.
- Four tracked Lesson filenames now match their frontmatter Service ids.
- `services/_TEMPLATE.md` no longer claims v1 has a Gateway or audit-driven freshness and now includes `endpoint.type: cli`.

### Safety
- Existing settings and instruction files are backed up before Client adapter changes.
- Client installs refuse malformed JSON and existing non-SSOT skill paths instead of replacing user state.
- Durable Memory promotion refuses unresolved placeholders, unconfirmed root causes, suspected secrets, and accidental overwrites.

## [1.1.0] - 2026-06-12

### Added
- `VERSION` file and this `CHANGELOG.md` — semantic versioning for the repo/package layer.
- `endpoint.type: cli` as a first-class connection backend in the lint contract and `install/schema/service.schema.json` (CLI backends like `op` and `osascript` were already in real use).
- `bin/portwright lint --strict` — fails notes whose frontmatter `status` is `stale` (exit 1).
- `bin/portwright doctor` per-adapter section — detects codex / hermes / gemini-cli installs, reports partial codex installs (`◐`), and points cursor to a manual User Rules check.
- New install adapters: `install/adapters/gemini-cli.md` and `install/adapters/cursor.md` (official Cursor User Rules mechanism).
- Schema↔lint parity tests, adapter packaging tests, and behavioral `--strict` tests (`tests/test_packaging.py`: 5 → 13 tests).
- `AGENTS_KR.md` — Korean twin for `AGENTS.md` per the repo's bilingual pair contract.
- `services/_drafts/` and `failures/_drafts/` directories (draft-first memory contract of `portwright-tool-memory`).
- Clean-clone install check section in `install/README.md`.

### Changed
- All 7 `services/*.md` notes re-verified against current official docs (2026-06-12); `last_verified` bumped with in-body evidence.
  - composio: Tool Router `create()` now defaults to all-toolkits sessions — the 2026-05-31 "meta-only session restriction" lesson is preserved with its rc-era context.
  - notion: current REST API version `2026-03-11` (data sources model) documented; pinned `2022-06-28` noted as working-but-legacy.
- `failures/` notes normalized to the frontmatter contract (3 missing frontmatter blocks added, 1 missing `service_version`, 1 filename fixed to `YYYY-MM-DD-...`).
- Merged remote `ca56372` (canonical home migration to `~/developer/tools/portwright`, skills package split, FUTURE-scope isolation in v1 skills).
- Global enforcer skill (`~/.claude/skills/external-tool-setup/SKILL.md`): 3 canonical-path references corrected to `tools/` (backup kept; purpose 6 was already present — stale `ACTIVE.md` record corrected).

### Fixed
- `bin/portwright`: dangling `--home` (no value) caused an infinite loop — now exits 2 with an error message (both `--home X` and `--home=` forms guarded).
- `services/apple-notes.md` frontmatter used `via:` instead of the contract key `server:`.
- `bin/portwright lint` baseline went from 13 passed / 9 failed to 22 passed / 0 failed.

### Security
- This release was prepared with symmetric gitleaks secret scans (same tool and pattern set for the pre-commit worktree scan and the pre-push full-history scan); the pre-commit worktree scan reported 0 findings. Scanning is a release-process practice, not a repo-shipped hook.
- `.gitignore` hardened: `.gjc/`, `.omo/`, `__pycache__/` added; existing `.env` / `*.key` / `secrets/` blocks kept.
[1.1.0]: https://github.com/foxion37/portwright/releases/tag/v1.1.0
[1.2.0]: https://github.com/foxion37/portwright/releases/tag/v1.2.0
[2.0.0]: https://github.com/foxion37/portwright/releases/tag/v2.0.0
[2.1.0]: ../../releases/tag/v2.1.0
