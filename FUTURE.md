# FUTURE — portwright deferred layers (past v1)

> The **north star**, not the current build. Active build = personal v1 (`README.md` / `HANDOFF.md`): Composio + fresh procedures, no gateway.
> Everything here is **deferred**. Don't build it until v1 proves out and the need is real.
>
> 🌐 Korean copy: **`FUTURE_KR.md`**. Edits here must be mirrored there (CLAUDE.md "Bilingual file convention").

---

## Why these are deferred
v1's purpose is minimize-user-intervention + minimize-agent-tool-use-cost. The two deferred layers below are different goals: **(A) a governance/safety gateway**, and **(B) org/multi-user scale**. Both were considered for v1 and cut — see ADR-0001/0002/0003 (all gateway-scoped, now FUTURE).

---

## Layer A — the governance/safety gateway

**What it is:** a self-hosted MCP gateway all clients pass through, enforcing what agents may do, blocking dangerous actions, and logging every call.

**Why deferred:** the six v1 purposes are about user-intervention and agent tool-use cost, not governance. A solo user's main client (Claude Code) already prompts before destructive actions, so the gateway's unique add (a hard-block floor across all clients + central allowlist + audit) only earns its operational cost once you run multiple clients heavily or need an enforced block floor.

**Lunar MCPX spike findings (ADR-0003) — read before reviving this:**
- Lunar's policy is **allow/block only**. There is **no CONFIRM / human-in-the-loop** action. So an AUTO/CONFIRM/FORBIDDEN three-tier model does NOT map — it collapses to allow/block. CONFIRM would have to live at the client or need a different engine.
- Deny-by-default works (`permissions: base: "block"`), with per-tool + wildcard allows.
- A queryable/exportable **audit log appears to be paid Control-Plane**, not OSS core (OSS audit is roadmap-stage). The audit-driven auto-failure pipeline (the old "moat") depends on this.
- Composio mounts as an upstream MCP service (likely; confirm live).

**What unlocks it:** you actually run 2+ clients heavily, OR want an enforced hard-block floor (e.g. block `export_secrets`) independent of each client's own permissions.

**`policy/tiers.yaml` and `allowlist/users.yaml`** on disk belong to this layer — they need a gateway to enforce. The three-tier `tiers.yaml` model needs rework against the allow/block reality before use.

**Engine candidates if revived:** Lunar MCPX (allow/block, OSS policy, paid audit), Lasso (security-first, PII redaction, prompt-injection defense — fits a regulated stage), ContextForge (IBM, MIT). For human-in-the-loop "confirm", neither Lunar nor a plain ACL gateway does it — you'd need an engine with interactive approval.

---

## Layer B — org / multi-user scale

**What it is:** turning the personal tool into something deployable to beginners in an org (e.g. your own organization), with governance and compliance.

| Capability | Why deferred | Unlocks at |
|------------|--------------|------------|
| Multi-user governance + role allowlist | Solo v1 has one user | Internal-org stage |
| Beginner deployment (URL + key handout) | No beginners yet | Internal-org stage |
| PII masking | No regulated data flowing | Regulated stage |
| Self-hosted Composio | Managed Composio is fine for personal; self-host removes the cloud from the credential path for data residency | Regulated stage |
| "Moat" = accumulated failure memory across many users | A moat needs scale; solo = thin stream. v1 treats `failures/` as a personal lesson cache | Multi-user stage |
| Publish `services/`+`failures/` schema as a standard | Validate the format in real use first | After internal-org stage |

**Sequence:** personal → internal org → published standard. Each stage starts only after the previous works.

---

## No-reinvention principle (still holds)
| Area | Reuse | We build |
|------|-------|----------|
| Connection / OAuth | Composio | — |
| Supervision-layer engine (Layer A) | MCP gateway (Lunar / Lasso) | — |
| Code-doc freshness | Context7 | — |
| **Connection AND usage procedures bound to a Profile + a tool-use failure/first-try-success cache + private notes + client-agnostic distribution** | (Composio Skills now returns `known_pitfalls`; the binding + privacy + distribution combo is still ours) | ★ our content |

## Prior-art survey (context)
No single OSS is 80%+ identical. Closest: GitAgent (~40%, git-native rules/memory), ReMe (~35%, success/failure pattern files), Reflexion (~30%), agentgateway (~30%, OSS data plane + RBAC), Composio (~25%, one-click OAuth), Context7 (~20%, doc freshness). The 2026-09-21 survey (`docs/research/2026-09-21-vnext-survey.md`) retired the old "nothing keeps procedure/failure knowledge" claim: **Composio Skills now returns execution order and `known_pitfalls`** in tool-search responses. The differentiator is no longer the cache's existence — it is **Profile binding** (account + env source + DB per project), **cross-backend Lessons**, **private notes** (`_private` roots), and **client-agnostic distribution** (`git pull` + `portwright update`).

## Deferred from 2.0.0 (2.1 candidates)
- **Private notes in a separate private remote.** Today `distributable: false` notes live in gitignored `_private/` roots on the workstation. Moving them to a separate private repository is deferred — the physical split already prevents distribution.
- **Per-client tier enforcement hooks.** The 2.0.0 tier is advisory and each Client's own permission prompt enforces it. Hooks that wire `confirm`/`forbid` into specific Clients are deferred until a Client needs more than its native prompt.
