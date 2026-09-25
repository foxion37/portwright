# ARCHITECTURE — portwright (Why this shape)

> Why personal v1 is shaped this way. To build, read `HANDOFF.md`. Deferred governance/gateway vision: `FUTURE.md`.
>
> 🌐 Korean copy: **`ARCHITECTURE_KR.md`**. Edits here must be mirrored there (CLAUDE.md "Bilingual file convention").

---

## 0. What this is
A **tool-use knowledge layer**, initially personal, now being extended to outside developers, company colleagues, and non-developers: minimize both the *user's* setup intervention **(A)** and the *agent's own* tool-use failures/tokens **(B)**. It remembers how each tool **connects** (via an independent credential-holding backend) and how to **use it correctly** (cached Procedures and Lessons). The personal installation already works; shared public and company Hubs are planned for M2 and M3. No gateway. Since **2.0.0** it is also a **profile router**: a Profile binds a project directory to its GitHub account, env source, and databases, so the cache answers for the *right* context (section 7).

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
| **D5** | Manage **content plus the minimal infrastructure that carries it**, engine-agnostic; `portwright update` remains the guarded code-update path and the tier remains **advisory** | The existing personal cache and one-way screened public code export remain separate; `scripts/export_public.py` selects from `install/public-export.json` and fails closed on personal markers, while this work repository remains private because old personal notes exist in its history. Planned shared delivery (M2 public, M3 company) uses **one Worker, one D1, and one Actions workflow per Hub**: D1 holds intake/events/token hashes/cost; the private commons git copy is canonical for shared note bodies, `grade`, and recall markers. Content sync to gitignored `_hub` is separate from code update; `_private` → tracked → `_hub` wins locally. Hub authentication and publication checks do not route or control external tool calls, so D1 still rules out a gateway, enforced policy, and an audit pipeline. ADR-0004 still rules out a hand-filled registry: a missing/stale central procedure can be derived from official docs and cached. See the [approved change request](CHANGE_REQUESTS.md). Existing 2.0.0 `update` evidence refresh remains as described in sections 4 and 7. |
| **D6** | Used by outside developers, company colleagues, and non-developers through a Client and Agent; public Operator and company administrator have distinct duties | The public Operator issues invite tokens, reviews `stable` promotions, and confirms `stable` recalls; the company administrator issues company tokens. The company Hub needs separate verified rights before M3. See the [approved change request](CHANGE_REQUESTS.md). |

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
| `services/_hub/`, `failures/_hub/` (planned M2) | Gitignored Hub content cache; lower precedence than `_private` and tracked notes | `.md` | local Agent |

Schema: service frontmatter `id, display_name, version_tag, last_verified, endpoint{type,server}, human_steps[], agent_can[], status` + optional `profile_ids[]`, `distributable`; failure frontmatter `date, service, service_version, status` + optional `profile_id`, `distributable`; profile frontmatter `id, project_path, github_account, env_source{kind,project,environment,injector}, databases[], services[], default_tier, host`. (`endpoint` is the MCP transport — see `CONTEXT.md`.)


## 6. What's deferred
Part of the former multi-user deferral is approved: M2 plans an invite-only public Hub and M3 plans a separately isolated company Hub, with content delivery rather than tool-call mediation. Still deferred: the entire governance/safety gateway, enforced policy/role allowlists, audit-driven failure collection, PII masking, and publishing the schema as a standard. Intake authentication and publication screening are not a gateway. See `FUTURE.md` and the [approved change request](CHANGE_REQUESTS.md); the six purposes in §1 and D1 remain unchanged.

## 7. Profile router (2.0.0)

**The problem it solves:** one operator runs several projects from one workstation, each with a different GitHub account, env source, and database. A v1 cache hit could return the *right Procedure for the wrong account* — the note was correct, the context was not. The Profile router fixes the boundary: `preflight` resolves *which project* it is serving before it answers *how*.

**Resolution order:** (1) `--profile <id>` if given; (2) longest `project_path` prefix match on the current directory; (3) if zero or 2+ candidates remain, a JEV **Choice** judgment picks among them; (4) unresolved → `profile: null` with a `rationale`, and preflight still returns the Procedure.

**What preflight returns (2.0.0):** the resolved `profile` (or null), the Procedure, active Lessons, `freshness` `{state: fresh|stale|unknown, compared, action: none|derive-required|verify-required}` from the deterministic table in D3, the advisory `tier`, and a `rationale`. Tier precedence: explicit forbid rule > matched rule (`install/tiers.json`) or Profile `default_tier` > JEV Score; unmatched or unknown → `confirm`; JEV never lowers an explicit forbid.

**Browser select:** `portwright browser select --candidates <file.json> --goal "<text>"` picks one BrowserCandidate (`{ref, role, name, url?, snippet?}`) via JEV Choice and refuses (`accepted=false`, `reject_reason`) on no candidates, confidence below 0.6, or a top-2 margin below 0.15. Extraction is Aside-Browser/Playwright's job; portwright only selects.

**JEV:** TypeSafe Jev, Choice/Score/Noul only, no text generation. The key (`TYPESAFE_API_KEY`) is injected at runtime via `opsvc` from 1Password and never stored; tests replay synthetic deterministic fixture doubles via `PORTWRIGHT_JEV_FIXTURES` (not recorded production/API responses).

**Evidence doc cache:** a valid distributable Procedure may declare `freshness_evidence.url` (final HTTPS URL of its public official doc; no auth/query/redirect URLs). `portwright update` downloads it during apply only — dry-run reports `docs_planned` and never downloads — and caches it at `services/_private/_evidence/<service>.json`. `preflight` auto-loads the cached doc when its URL matches and no explicit evidence flags were passed, reporting URL, digest, and fetched_at; the doc feeds the advisory Noul check but a download alone never proves `fresh`.
