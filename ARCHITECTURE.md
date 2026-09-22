# ARCHITECTURE — portwright (Why this shape)

> Why personal v1 is shaped this way. To build, read `HANDOFF.md`. Deferred governance/gateway vision: `FUTURE.md`.
>
> 🌐 Korean copy: **`ARCHITECTURE_KR.md`**. Edits here must be mirrored there (CLAUDE.md "Bilingual file convention").

---

## 0. What this is
A **personal tool-use knowledge layer**: minimize both the *user's* setup intervention **(A)** and the *agent's own* tool-use failures/tokens **(B)**. It does this by remembering two things per tool — how it **connects** (credentials remembered, via any backend) and how it is **correctly used** (so the agent succeeds first-try instead of re-failing the same way). One user (me), agents = Claude Code / Cursor / ChatGPT. No gateway to run. Since **2.0.0** it is also a **profile router**: a Profile binds a project directory to its GitHub account, env source, and databases, so the cache answers for the *right* context (section 7).

## 1. The purposes (the actual goal)

**Two ends, six purposes.** (A) minimize the *user's* intervention; (B) minimize the *agent's own* tool-use failures/tokens.

*(A) Minimize user intervention*
1. Minimize the external-tool setup the user has to do.
2. Don't make the user re-connect a tool already connected.
3. Don't instruct the user to do things they don't need to do.
4. Net: take the minimal initial token/API key once, remember it, do the rest automatically.

*(A+B) Right procedure, every time*
5. Keep each tool's procedure current/correct so **neither the user nor the agent** is sent down a stale or wrong path — *freshly derived* for the human step (e.g. "Dashboard → enter API key" when it's now "Dashboard → Settings → API/OAuth → Projects → Key"), and *cached-and-correct* for the agent's own calls.

*(B) Minimize agent tool-use cost*
6. Minimize the agent's own tool-use cost: cache hard-won connection **and usage** procedures and failures so the agent succeeds **first-try** next time instead of re-paying the trial-and-error in tokens.

Purposes 1-4 are **connection/credential** problems → solved by a connection backend — **Composio is one option; a native MCP / CLI / direct API is an equal alternative.** Purposes 5-6 are **procedure-knowledge** problems → this repo's `services/` + `failures/` + the verify-before-instruct mechanism solve them. None of the six is a governance/blocking problem, so v1 has no gateway.

## 2. v1 decisions (locked)

| # | Decision | Rationale |
|---|----------|-----------|
| **D1** | **No gateway in v1.** | The six purposes are minimize-user-intervention + minimize-agent-tool-use-cost, not governance. A supervision gateway (policy/audit/blocking) serves a different goal → `FUTURE.md`. A solo non-dev shouldn't run a 24/7 Docker service it doesn't need. |
| **D2** | A **connection backend** = connection/OAuth, remembers credentials. **Composio is one option; native MCP / CLI / API are equals.** | Addresses purposes 1-4 (connect-once, no token re-request). Whichever backend can actually execute the task is the right one — native Vercel CLI/MCP carried the real deploy here because Composio's session had been minted meta-only (a self-config slip, since diagnosed + fixed — see `services/composio.md`), and native was unblocked + closer. Composio also executes once its session includes the app toolkit and the app is OAuth-connected. Composio is safe for personal use (SOC2/ISO, brokered tokens never enter the LLM) but is not the identity. |
| **D3** | **Right-procedure-every-time via verify-before-instruct + a tool-use knowledge cache, now with a deterministic freshness table + JEV support** | Solves purposes 5-6. For the human step: derive the path fresh from current docs before instructing. For the agent's own calls: read the cached procedure/lesson first so it doesn't re-fail. Since 2.0.0, `preflight` also reports a freshness state from a deterministic decision table (section 7): evidence version equal to `version_tag` → `fresh`, different → `stale`; `fetched_at` only → `fresh` when `<= last_verified`, else `unknown` + verify-required; no evidence → `unknown` + verify-required; a missing credential or judgment only removes the Noul support line (rules 1-2 are deterministic and need no JEV; when the JEV question would have been asked but no credential or fixture answered it, the rationale says so). `unknown` is never `fresh`. TypeSafe JEV (Choice/Score/Noul, decision-only, no text generation) supplies cheap judgments — route choice, Noul freshness support, menu selection, tier scoring — and Noul can strengthen a stale rationale but never flips a deterministic result. This is the IP — "Context7, but for tool connection **and usage** procedures." |
| **D4** | Data = `services/` + `failures/` + `profiles/` (+ `_private` roots) | `services/` = current procedures; `failures/` = lessons from any tool-use failure — a stale/wrong human-step path **or the agent's own runtime mistake** (e.g. wrong SDK/param). Since 2.0.0: `profiles/` = per-project context bundles, and `services/_private/` + `failures/_private/` (gitignored) hold `distributable: false` notes — personal notes never leave the workstation but are read by the same catalog. `policy/` + `allowlist/` need a gateway to enforce → `FUTURE.md`. |
| **D5** | We manage only the **content**, engine-agnostic; distribution is `portwright update` (it performs its own guarded `git pull --ff-only` — never pull first); the tier is **advisory** | A connection backend (one of several — Composio / native MCP / CLI) handles connection; the agent (Claude Code) reads procedures + lessons. Don't build infrastructure. The content (`services/` + `failures/` + `profiles/`), not any one backend, is the asset. Since 2.0.0 the content distributes through `portwright update`, which fetches and fast-forwards itself after a conflict check, re-checks stale candidates against their local contracts (`revalidated` counts local note schema validation only), refreshes cached official-doc evidence during apply for Procedures declaring `freshness_evidence.url` (dry-run only plans, never downloads; `docs_failed` exits nonzero; unconfigured notes are reported, not pretended refreshed), and reports private exclusions and refused conflicts. The preflight `tier` (`auto|confirm|forbid`) is **advice, not a gateway** — clients enforce `confirm`/`forbid` through their own permission prompts. Since 2026-09-22 distribution has a second, one-way path: `scripts/export_public.py` builds the public tree for a separate public repository with fresh history, selected mechanically from `install/public-export.json` (`distributable: true` notes only, `_private`/`_drafts`/`profiles`/personal skills refused) and gated by regex personal markers that abort the export before any file is written. This repository stays private permanently, because notes now under `_private/` were once committed at public paths. |
| **D6** | Operated by **me + Claude Code, solo** | Favor zero standing infrastructure. |

## 3. Why no gateway (the thing we removed)
Earlier drafts centered a supervision gateway (Lunar MCPX) for blocking dangerous actions, audit, and multi-client policy. A spike (`docs/adr/0003`) plus the restated purpose showed: (a) the gateway's value is *governance*, which isn't in the six purposes; (b) Lunar's policy is allow/block only (no human-in-the-loop "confirm"); (c) its queryable audit log is likely a paid tier. So the gateway and everything it enforces moves to `FUTURE.md` as an optional safety layer. Claude Code's own permission prompts cover destructive-action safety for v1.

## 4. The tool-use knowledge mechanism (purposes 5-6, the IP)
- `services/` is a **Procedure cache**, not a pre-filled registry. A `services/<id>.md` exists only once a Service has been used and verified.
- **Two modes, one mechanism:** (i) *human step* — derive the path **fresh** from the tool's current docs right before handing it over (always current), and may cache it; (ii) *agent's own calls* — read the cached procedure/lesson **first** so the agent doesn't re-pay a failure it (or a past run) already hit. Either way, a wrong/hard-won path becomes a **Lesson** in `failures/`. So the agent's job isn't "freshness-exempt" — it's *cache-served* (purpose 6).
- This is general — it covers every tool, not a hand-written list. Since 2.0.0, `portwright update` re-checks stale candidates after its own guarded fast-forward pull (a note whose `status` is `stale` or whose `last_verified` is 90+ days old is re-validated against its local contract and reported) and refreshes the cached evidence doc for Procedures that declare `freshness_evidence.url` — a downloaded doc is evidence input, not a freshness proof, so distribution also refreshes trust. (Full audit-log-driven auto-tracking stays in `FUTURE.md`.)
- A connection backend (Composio when it can execute, else native MCP/CLI) eliminates most manual steps entirely (connect-once), so the surface for stale *human* instructions shrinks to genuine first-time setup — while the *agent-usage* cache keeps paying off on every call.

## 5. Content structure
| Folder | Role | Format | Consumed by |
|--------|------|--------|-------------|
| `services/` | Procedure **cache** (derive-on-miss, not pre-filled) | `.md` (frontmatter + body) | the agent |
| `failures/` | Lesson cache (any tool-use failure — stale/wrong human-step path or agent's own runtime mistake) | `.md` (frontmatter + body) | agent + me |
| `profiles/` | Profile router table — one file per project context (path, GitHub account, env source, databases, default tier, host) | `.md` (frontmatter + body) | `preflight` |
| `services/_private/`, `failures/_private/` | Private roots — `distributable: false` notes, gitignored, same catalog | `.md` (frontmatter + body) | agent + me |

Schema: service frontmatter `id, display_name, version_tag, last_verified, endpoint{type,server}, human_steps[], agent_can[], status` + optional `profile_ids[]`, `distributable`; failure frontmatter `date, service, service_version, status` + optional `profile_id`, `distributable`; profile frontmatter `id, project_path, github_account, env_source{kind,project,environment,injector}, databases[], services[], default_tier, host`. (`endpoint` is the MCP transport — see `CONTEXT.md`.)


## 6. What's deferred
The entire governance/safety gateway — Lunar MCPX, policy tiers, CONFIRM, audit-driven failure collection, role allowlists, PII masking, multi-user, publishing the schema as a standard. See `FUTURE.md`, including the Lunar spike findings (ADR-0003) for whoever revisits the gateway.

## 7. Profile router (2.0.0)

**The problem it solves:** one operator runs several projects from one workstation, each with a different GitHub account, env source, and database. A v1 cache hit could return the *right Procedure for the wrong account* — the note was correct, the context was not. The Profile router fixes the boundary: `preflight` resolves *which project* it is serving before it answers *how*.

**Resolution order:** (1) `--profile <id>` if given; (2) longest `project_path` prefix match on the current directory; (3) if zero or 2+ candidates remain, a JEV **Choice** judgment picks among them; (4) unresolved → `profile: null` with a `rationale`, and preflight still returns the Procedure.

**What preflight returns (2.0.0):** the resolved `profile` (or null), the Procedure, active Lessons, `freshness` `{state: fresh|stale|unknown, compared, action: none|derive-required|verify-required}` from the deterministic table in D3, the advisory `tier`, and a `rationale`. Tier precedence: explicit forbid rule > matched rule (`install/tiers.json`) or Profile `default_tier` > JEV Score; unmatched or unknown → `confirm`; JEV never lowers an explicit forbid.

**Browser select:** `portwright browser select --candidates <file.json> --goal "<text>"` picks one BrowserCandidate (`{ref, role, name, url?, snippet?}`) via JEV Choice and refuses (`accepted=false`, `reject_reason`) on no candidates, confidence below 0.6, or a top-2 margin below 0.15. Extraction is Aside-Browser/Playwright's job; portwright only selects.

**JEV:** TypeSafe Jev, Choice/Score/Noul only, no text generation. The key (`TYPESAFE_API_KEY`) is injected at runtime via `opsvc` from 1Password and never stored; tests replay synthetic deterministic fixture doubles via `PORTWRIGHT_JEV_FIXTURES` (not recorded production/API responses).

**Evidence doc cache:** a valid distributable Procedure may declare `freshness_evidence.url` (final HTTPS URL of its public official doc; no auth/query/redirect URLs). `portwright update` downloads it during apply only — dry-run reports `docs_planned` and never downloads — and caches it at `services/_private/_evidence/<service>.json`. `preflight` auto-loads the cached doc when its URL matches and no explicit evidence flags were passed, reporting URL, digest, and fetched_at; the doc feeds the advisory Noul check but a download alone never proves `fresh`.
