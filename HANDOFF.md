# HANDOFF — portwright build order (v1 + 2.0.0)

> Paste into a coding agent to start. Background: `ARCHITECTURE.md`. Deferred gateway vision: `FUTURE.md`. Schemas: each folder's `_TEMPLATE`.
>
> 🌐 Korean copy: **`HANDOFF_KR.md`**. Edits here must be mirrored there (CLAUDE.md "Bilingual file convention").

---

## 0. Mission (v1)
A personal layer that (1) connects each external tool once via a connection backend (Composio or native MCP/CLI) and remembers it, (2) makes sure any manual step an agent gives the user comes from the *current* procedure, never a stale path, and (3) caches the procedure/failure the agent hit so it succeeds first-try next time instead of re-paying the trial-and-error. No gateway. Operated by me + Claude Code, solo.

## 1. Locked decisions (don't re-litigate)
| Item | v1 decision |
|------|-------------|
| Scope | Minimize user intervention + minimize agent tool-use cost (connection + usage knowledge). Governance/gateway → `FUTURE.md` |
| Connection | A connection backend (**Composio** OR native MCP/CLI), connect-once + remembered credentials |
| Freshness | **verify-before-instruct** — read the procedure + check it's current before instructing a manual step |
| Data | `services/` (current procedures) + `failures/` (lessons). `policy/`+`allowlist/` → FUTURE |
| Infra | **None.** No gateway, no policy engine, no audit pipeline |
| Operator | Me + Claude Code, solo |

## 2. Absolute rules
1. **Don't build infrastructure you don't need.** No gateway in v1.
2. **Never put tokens/secrets in code, docs, repo, or examples.** Composio holds credentials; runtime only. (`.gitignore` blocks `.env`, `*.key`, `secrets/`.)
3. **Stamp `last_verified` on every procedure.** A procedure with no recent verification is suspect.
4. **`human_steps` = only unavoidable human steps**, and they must reflect the *current* tool UI. "Get a token / sign up / configure it yourself" beyond the unavoidable minimum is the anti-pattern this project exists to kill.
5. **Verify before you instruct.** Never hand the user a setup step without confirming the procedure is current.

## 3. Build order
1. **Connect a backend** for the one tool you use most (Composio if it executes; else native MCP/CLI) (e.g. Vercel). One-click OAuth, once. Confirm the agent can act through it without asking you for a token again. This delivers purposes 1-4.
2. **Build the procedure-resolution rule (general, all tools).** Expose it via a Claude Code skill or `CLAUDE.md` rule. The rule: before instructing any manual step, look for `services/<id>.md`; if fresh, use it; if **missing or stale, derive the current procedure from the tool's official docs and cache it** to `services/<id>.md` (bump `last_verified`); record a `failures/` lesson if a stale/wrong path was caught. This is the IP and it must be tool-agnostic — never a per-tool hand-written list.
3. **Let the cache fill itself — Vercel is just the first entry.** Don't hand-author a library. Run the loop on Vercel: the agent derives Vercel's current procedure, instructs you through the one-time bits, and writes `services/vercel.md` as the first cached, verified entry. (You may seed it once as a worked example, but the mechanism, not manual authoring, is the point.)

## 4. v1 done = prove it works (by demonstration, not token metrics)
> The agent does a real task on your most-used tool (e.g. deploy to Vercel) through **any** connection backend — with **minimal user intervention** (no token re-request, no manual-dashboard step). And the procedure/failure it hit is **cached**, so a 2nd run skips the same trial-and-error: when a tool's setup path has changed, the agent **catches it / refreshes it** instead of sending you down the old path; when the agent's own call already failed once, the cached lesson lets the 2nd run avoid it. Prove end (B) by **demonstration** — a 2nd run that visibly avoids the cached failures — not by counting tokens.
>
> **2.0.0 adds its own demonstrations:** a profile-switch demo (two Profiles with different GitHub accounts and databases each return the right context), a freshness demo (an intentionally aged Procedure is judged `stale`, and missing-evidence cases return `unknown` + verify-required, never `fresh`), the JEV-vs-LLM cost table (`docs/research/2026-09-21-jev-cost.md`), and the existing gates green (unittest >= 41, `check` 0 failed, recorded in `docs/research/2026-09-21-v2-validation.json`).

## 5. Don'ts (anti-patterns)
- ❌ Standing up a gateway / policy engine / audit pipeline in v1 (all `FUTURE.md`).
- ❌ Hand-authoring a service library by hand (derive-on-miss instead).
- ❌ Instructing a manual step without checking the procedure is current.
- ❌ Omitting `last_verified`. ❌ Hardcoding tokens anywhere.

## 6. Reference docs
- `ARCHITECTURE.md` — v1 decisions and why no gateway.
- `README.md` — folder roles + v1 shape.
- `MAINTENANCE.md` — keeping procedures fresh, solo.
- `FUTURE.md` — the deferred gateway/governance layer (+ Lunar spike findings, ADR-0003).
- `services/_TEMPLATE.md`, `failures/_TEMPLATE.md` — schemas.

## 7. 2.0.0 build order
Approved 2026-09-21 (`CHANGE_REQUESTS.md`). Seed: `~/.ouroboros/seeds/portwright-v2-profile-router-20260921.yaml`. Survey: `docs/research/2026-09-21-vnext-survey.md`. D1 still holds: no gateway, the tier is advisory.

1. **Profiles + profile-aware preflight.** `profiles/<id>.md` (path, GitHub account, env source, databases, services, default tier, host); `preflight` resolves the Profile by longest `project_path` prefix on cwd, JEV Choice on ambiguity, `null` + rationale when unresolved.
2. **Freshness.** The deterministic decision table (version match → fresh/stale — a match proves the versions agree, not that the whole procedure is still correct; `fetched_at` only → fresh if `<= last_verified` else unknown + verify-required; missing evidence → unknown + verify-required; a missing JEV credential or judgment only drops the advisory Noul support line — it never forces `unknown` when a deterministic rule already answered; JEV Noul only strengthens stale rationale). `unknown` is never `fresh`.
3. **Advisory tier.** Explicit forbid > matched rule (`install/tiers.json`) or Profile `default_tier` > JEV Score; unmatched/unknown → `confirm`. Clients enforce; portwright only advises.
4. **Browser select.** `portwright browser select` picks one candidate via JEV Choice; refuses on no candidates, confidence < 0.6, or top-2 margin < 0.15.
5. **Update + private split.** `portwright update` (runs its own guarded `git pull --ff-only` after a conflict check — never pull first; stale re-validation against local contracts, evidence-doc refresh for Procedures declaring `freshness_evidence.url` on apply only, private exclusions, unclassified, refused conflicts); `distributable` field; `services/_private/` + `failures/_private/` gitignored roots.
6. **Cost table.** One live measurement session (N>=20 paired LLM vs JEV judgments) → `docs/research/2026-09-21-jev-cost.md`.
7. **Validation record.** Gates + update scenarios + private manifest → `docs/research/2026-09-21-v2-validation.json`.
