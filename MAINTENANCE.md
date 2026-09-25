# Maintenance — portwright v1 (solo)

This layer survives on two things: **(1) we manage only the content (procedures + lessons). (2) we keep procedures current so the agent never instructs a stale path.**

> 🌐 Korean copy: **`MAINTENANCE_KR.md`**. Edits here must be mirrored there (CLAUDE.md "Bilingual file convention").

## Division of roles
- A connection backend (Composio or native MCP/CLI) = where credentials live. I connect tools once, via whichever backend can execute.
- **Content (this repo)** = mine: `services/` procedures + `failures/` lessons. Small, light.
- **No gateway to run.** Nothing standing to babysit. (The gateway is `FUTURE.md`.)

## The routine — fresh for the human step, cached for the agent's calls
For the **human step** (a step the agent hands to the user), derive the path fresh from current docs right before instructing. For the **agent's own calls**, read the cached procedure/lesson first so it doesn't re-fail — cache-served, not freshness-exempt. There's no audit log to read and no service to keep alive.
1. **Derive-at-instruct.** Right before the agent hands the user a manual step, it derives that path from the tool's current docs — always fresh. (No `last_verified` tracking needed in v1; there's no audit log to auto-track it.)
2. **Preflight before action.** Run `bin/portwright preflight <service-id>` so the Agent reads the current Procedure and active Lessons before calling the tool. Use `--intent instruct` before any human instruction.
3. **Catch-and-fix.** When a step or tool call fails, confirm the root cause, create a Lesson draft with `bin/portwright memory draft lesson <service-id> <slug>`, then promote it only after `memory review` passes.

## A new service isn't "added" — it's cached on first use
You don't hand-author procedures. On `DERIVE REQUIRED`, the Agent checks current official docs, creates a draft with `bin/portwright memory draft procedure <service-id>`, and promotes the verified Procedure. The only human step is the one-time backend connect (one-click OAuth — Composio or native MCP/CLI). No config to compile, no gateway to update. (Minimize human hands — manual authoring is a defect to design out.)

## Rot check
- Run `bin/portwright check` to validate durable notes and `_drafts/` before promotion. Since 2.0.0 it also checks the `_private` roots.
- A `failures/` lesson whose `service_version` differs from the current version → `status: stale` candidate (a fix true on an old version may be wrong now).
- Since 2.0.0, `bin/portwright update` performs its own guarded `git pull --ff-only` after a conflict check — never run `git pull` first. It then re-checks stale candidates: a tracked note whose `status` is `stale` or whose `last_verified` is 90+ days old is re-validated against its local contract and counted in `revalidated` (local note schema validation only). Separately, a valid distributable Procedure that declares `freshness_evidence.url` (the final HTTPS URL of its public official doc — no auth, query, or redirect URLs) gets that document refreshed during apply and cached at `services/_private/_evidence/<service>.json`. Run `bin/portwright update --dry-run --json` first: it reports `docs_planned` without downloading anything, plus `stale_candidates`, `private_excluded`, `unclassified`, and `refused_conflicts`. The apply run reports `docs_fetched`, `docs_changed`, `docs_failed`, and `docs_unconfigured`; a non-empty `docs_failed` exits nonzero, and Procedures without the field are listed under `docs_unconfigured` rather than pretended refreshed. Then run `bin/portwright update`.
- `preflight` auto-loads the cached evidence doc when its URL matches the note's `freshness_evidence.url` and no explicit evidence flags were passed; it reports the URL, digest, and fetched_at. A downloaded doc alone never proves freshness — `fresh` still requires an explicit verified version match (or `fetched_at <= last_verified`).
- (Full audit-log-driven freshness stays in `FUTURE.md`; the 2.0.0 freshness decision table in `ARCHITECTURE.md` section 7 covers what preflight reports.)

## Classifying a note (2.0.0)
- Every tracked note carries `distributable: true|false`. `true` ships through `update`'s own fast-forward pull; `false` belongs in `services/_private/` or `failures/_private/` (gitignored, still read by the same catalog and checked by `check`).
- Mark `false` when the body or frontmatter names a specific account, host, private path, internal project, or vault item. Generic public-SaaS procedures stay `true`.
- Tracked notes missing the field show up in `update`'s `unclassified` list — classify them instead of ignoring.

## JEV fixture tests (2.0.0)
- Judgment code paths never need a live key in tests: set `PORTWRIGHT_JEV_FIXTURES=tests/fixtures/jev` and the client replays synthetic deterministic test doubles (not recorded production/API responses). With no `TYPESAFE_API_KEY` and no fixture, judgments return `unknown` with a secret-safe error — that is the intended fallback, not a bug.

## Personal remote skills and document monitoring

- Screen complete local packages with `python3 scripts/classify_personal_skills.py screen`. A candidate is not publication approval. Blocked/private packages stay local; uninspected files and external symlinks must not be silently skipped.
- Approved immutable copies live under `skills/personal/<name>/`; `install/personal-skills-inventory.json` binds every approved resource path, byte size and SHA-256. Preserve supporting files and licenses. Do not change the original installed skills.
- One Actions publisher builds the union of existing Procedures, packaged Portwright guides and approved personal skills. Both per-package and global resource lists must agree exactly. Missing, renamed, additional or modified personal resources require new approval before any model call or publication.
- Personal packages matching their approved inventory are not sent to JEV. Public Procedure checks remain separate. A name-only publication exception is not sufficient.
- The authenticated gisul registry serves instructions and supporting resources, not remotely executed plugin code. Its reader verifies file digests. Unknown/binary resource types can be preserved in storage while the instruction-only reader refuses to expose them as text.
- Cron reads digest-bound `watch-sources.json` from the current immutable release. First collection establishes a baseline without JEV; later changes are judged. Pending judgments and issue delivery remain retryable. Returning to the first document is still a change.
- The configured schedule is `17 3 * * *` UTC (12:17 Asia/Seoul). Always set it with `wrangler deploy --triggers '<cron>'`; a plain deploy does not restore the configured value, and the printed `schedule:` line is intent rather than proof. Confirm by watching `watch/state.json`. Alerts are GitHub Issues containing source URL, hashes, model probability and release commit, never document bodies or credentials.
- `GISUL_GITHUB_TOKEN` in this personal deployment is limited to Issues read/write on `<your-account>/<your-private-repo>`, plus required Metadata read. It is not a content-writing credential; do not enable gisul write tools with it.
- The Mac mini reader has only a reader credential. Deployment code/runtime preparation does not grant Cloudflare administration; authenticate that host separately if deployment authority is later approved.

## Public distribution export (2026-09-22)
- This repository is the private workstation repository and stays private permanently: every note now under `_private/` was once committed at a public path, so its history can never become a public origin. The public repository is created empty, with fresh history, and is never a fork, clone, or filtered copy of this one.
- Build the public tree with `python3 scripts/export_public.py --dry-run` first, then `python3 scripts/export_public.py --out build/public`. Review the printed file list before the first publication.
- The export set is mechanical, from `install/public-export.json`: include prefixes/files minus excludes, and under `services/`/`failures/` only valid notes with `distributable: true`. `_private`, `_drafts`, `_evidence`, and `profiles` are refused by the shared bundle guard, which also rejects symlinks, binaries, oversize files, and suspected secret material. `skills/personal/`, `docs/research/`, `.github/`, the personal inventory and gate-allow files, and the two personal scripts are excluded by name.
- Personal identifiers are generalized by `substitutions` and then gated by regex `personal_markers`. Any surviving marker aborts the run before a single file is written. Adding a personal-looking string to a published doc is therefore a build failure, not a review item.
- Verify an export by running its own suite inside the output tree (`cd build/public && python3 -m unittest discover -s tests -q`) and `./bin/portwright check`. Remove generated `__pycache__` before committing; the exported `.gitignore` already excludes it.

## Improvement review (2026-09-22)
- `bin/portwright review [--days 90] [--json] [--no-jev] [--no-write]` proposes what to fix next. It counts; it does not guess. Findings: a Procedure whose Lessons keep arriving (`procedure-defect`), repeated preflights with no Procedure (`cache-gap`), repeated `unknown` freshness on a note without `freshness_evidence.url` (`no-evidence`), a used note past 90 days (`stale`), and an active Lesson its Procedure never references (`unlinked-lesson`). Old failures that stopped are treated as fixed and are not reported.
- The signal comes from two local sources. The note corpus includes `_private` roots, because those are the notes actually used here; only `_drafts` are ignored. The usage ledger `_local/usage.jsonl` gets one line per `preflight` with service id, profile id, freshness state, tier, intent, and Procedure path — never arguments, results, working directories, or credentials. `_local/` is gitignored and excluded from the public export; deleting it loses history but breaks nothing, and a truncated line is skipped rather than fatal.
- JEV is optional and may only reorder findings through one request; it never adds, drops, or edits one. Without a credential the order stays deterministic and the report says so.
- Reports are saved to `_local/reviews/<date>.md` unless `--no-write`, so a later session can see which proposals were acted on.
- Reviewing is how private use drives public releases: the findings are the agenda for the next export, instead of a schedule nobody is motivated to keep.


## Failure modes
- **Stale procedure slips through** → the user gets sent down a wrong path. Mitigation: verify-before-instruct is the rule, not an afterthought.
- **Composio down** → the agent can't act on connected tools. It's a managed service; check Composio status, no local fix.
