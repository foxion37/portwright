# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.2.3] - 2026-09-26
### Changed
- Lesson submissions are judged on whether the official document covers the same service feature without contradicting the note. They are no longer judged on whether it "substantively supports" the note. Procedures keep the original question.
- Official documents are masked (`[redacted]`) where they match a secret or identifier pattern before judgment, instead of being refused. Submitted text is still rejected.
- The model request limit rises from 24,000 to 32,000 bytes. Longer requests are still held, never truncated.
- Gate policies bind a digest of the question set. Proofs made under older questions, pricing, or identifier policy are withheld until re-judged. `gate_version` stays `hub-gates-v1`, so existing clients keep syncing.

### Fixed
- The `absolute-home` identifier rule no longer matches API paths such as `gmail/v1/users/me/`.
- The Hub verifier's wire test derives the note filename date from the server's UTC creation day, so it no longer fails after midnight UTC.
- The live injection verifier accepts both gate ④ security refusals the seed allows: a malicious hold (`injection_suspected`) and a personal-data rejection (`identifier`). Each still needs a model reservation, so an early gate ① rejection cannot pass. On v2.2.2 the model rejected one fixed attack as `identifier`, which the verifier had wrongly treated as a mismatch.

## [2.2.2] - 2026-09-26
### Fixed
- One-time note migration now processes only the paths listed in the evidence manifest. A tracked `failures/_TEMPLATE.md` failed note-ID parsing and aborted the whole run before any gate ran.
- Migration model calls now reserve budget as operator `bundle` work instead of `intake` work. A migrated note has no intake row, and the Hub's foreign key rejected the reservation with HTTP 500, so every live migration gate was held as `budget_unavailable`.
- The model gate now reports security verdicts before evidence sufficiency: a personal-identifier suspicion rejects as `identifier` and a malicious suspicion holds as `injection_suspected` even when document support is also low. Live JEV scored all ten fixed injection lessons malicious ≥ 0.91, but they were reported as `evidence_not_specific` because support was checked first.
- `verify_hub.py` normal controls now cite official pages that pass the live gate. Six fixed normal lessons and the default synthetic note cited pages that exceed the 24,000-byte model request, redirect, exceed 1 MiB, or contain example credentials, so every live scenario would have held its control. Synthetic lesson dates now follow the current UTC day, which the gate requires to match the server-generated filename.
- The live Hub verifier now handles Cloudflare's optional R2 pagination metadata on short final pages, reads logging settings from `/script-settings`, waits for a real tail attachment, and correlates redacted authenticated request URLs via a benign header. Personal v1 URI verification uses the deployed `skill://gisul/portwright/personal/` namespace; recall verification follows §7.3 by allowing an old pinned package to disappear while still requiring other notes in the current release. Synthetic recall controls now cite supported documentation rather than an unrelated instruction.

## [2.2.1] - 2026-09-26
### Fixed
- Hub sync, local MCP relay, and live verifier now send a stable User-Agent. Cloudflare workers.dev returned error 1010 to Python's default User-Agent; authenticated sync was blocked before reaching the Worker.

## [2.2.0] - 2026-09-25
### Added
- Public Hub Worker: scoped MCP lesson intake, D1 token lineage and budgets, immutable R2 releases, recall-aware reads, and operator administration.
- Commons publisher: one workflow with evidence gates, review receipts, safe git commits, bundle publication, and resumable acknowledgments.
- `portwright hub sync`: verified public/company generations with offline recall enforcement, private overrides, explicit trial opt-in, and local MCP submission screening.
- `scripts/verify_hub.py` and fixed synthetic corpora for M2 Hub acceptance. Completion literals require deployed HTTP and independent D1/commons git/tail/R2/model evidence as applicable; missing credentials or incomplete channels block them. `--setup`/`--teardown` can use a run-ID recovery manifest containing only identifiers and token hashes, never credential values.
- When Hub verification lacks credentials, it re-executes once through the approved headless 1Password runner with an owner-only reference file. Injection errors remain `BLOCKED` without printing runner output or values.

### Fixed
- M2 acceptance consumes H2/H3/H4 digest-bound `/sync`, real local sync, default/opt-in preflight and MCP `get_note`, and recalled-generation rejection. Pinned directory recall now preserves the pre-recall sibling set at the same canonical parent and pin; NOT_FOUND is allowed only for an observed empty remainder.
- Secrets verification sends every corpus sample through all string leaves of submit, confirm and report, plus RPC IDs, unknown keys/values, JSON escapes, malformed JSON, invalid UTF-8 and separate complete oversized frames. Every request has a non-secret URL marker; the verifier waits for completed invocation receipts and controls, joins both tail readers and scans the final snapshot. Missing receipts, partial frames, sampling and reconnect notices block success.
- D1 scans project the intake/events TEXT columns for all token hashes created since the verification window began, paging in memory with explicit row-count and page-completeness checks. Other users' text is never written to artifacts or logs; SQL argv contains no corpus samples. Wrangler file logging and metrics remain disabled. R2 establishes an in-memory baseline from complete listings and every original object body (including an initially empty bucket), retaining ETags and SHA-256 digests. The ending sweep reads every body again, scans new/changed objects, and checks unchanged ETags against baseline digests. Refused HTTP bodies are checked in memory for sample echoes.
- R2 absence checks also inspect decoded JSON string keys/values, so escaped samples cannot hide in inventories; a disappearing baseline object blocks verification instead of being treated as clean.
- Teardown verifies rejection and text erasure for pending/held intake, token revocation, recalled revision absence, and removal of the budget override. Review uses a fresh detached checkout under `/tmp`, without switching the supplied commons checkout.
- Idempotency now observes two distinct queued/running dispatch IDs while the same intake or promotion review remains pending, then waits for those exact runs and verifies one intake/frontmatter receipt, commit trailer and review ack/promotion. Cancelled, collapsed, already-finished or otherwise unproven overlaps block acceptance. Synthetic subprocess tests separate HTTP 204 from completion, record the pending-work intersection, and serialize mutations like the workflow concurrency group. Local H2 rejection/directory and real H4 consumer checks remain synthetic evidence, not deployed acceptance.

### Limitations
- Live acceptance requires approved short-lived credentials and scoped independent read access, verified model pricing, and the operator's approved headless injection command. H4 is integrated; a CLI help response alone is not preflight proof. No deployment or live acceptance was performed here.
- Tail completeness is scoped to marked requests' completed invocation events and the captured window, not provider-internal logs. A platform that cannot deliver every unsampled receipt is BLOCKED; an end marker or quiet period alone never proves completion.
- R2 verification covers two complete observed inventories and their bodies: O(total retained bytes), plus paginated listings. Samples fail verification; unreadable/oversized objects, incomplete pagination, a changing sweep or an unchanged ETag with a mismatched body digest block it. Objects created and deleted between sweeps remain unobserved, so this is not a global or continuous absence claim. Independent continuous mutation history is not required by §9.3-4; the unconditional history-related block was removed. `SECRETS OK` remains reachable only after all required observation channels and teardown succeed.
- The budget fake Worker tests the verifier's response handling and cleanup, not H2's conditional INSERT or deployed D1 atomicity. Those require H2's own runtime tests and a separately authorized deployed budget race.
- Lifecycle and recall reissue **consume the supplied `HUB_TEST_SUBMIT_A_TOKEN` permanently**. Use a fresh disposable A token for each run; teardown revokes the replacement and cannot restore the original.
- Budget verification refuses a pre-existing override instead of overwriting operator state. Workflow evidence assumes an exclusive verification window with no unrelated manual dispatches. Company isolation requires a public comparison Hub; a personal comparison does not establish the M3 boundary.
- Injection success requires the actual `injection_suspected` gate outcome and model ledger evidence, not any held/rejected state. Changed official documents or earlier evidence-gate refusals stop the literal. H3 exports no gate-reason constant set; corpus expectations are checked against its current wire reasons. Off-domain fetch absence is supported by the H3 local gate tests, not observable from an empty remote model ledger alone.
- Commit/upload interruption and resume remain H3 tempfile-git test evidence, not part of the live idempotency literal. Model digest/byte receipts and local transport tests are not a recording of provider request bodies or a guarantee about platform-internal logs.


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
[2.2.0]: ../../releases/tag/v2.2.0
[2.2.1]: ../../releases/tag/v2.2.1
[2.2.2]: ../../releases/tag/v2.2.2
[2.2.3]: ../../releases/tag/v2.2.3
