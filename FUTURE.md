# FUTURE — portwright deferred layers (past v1)

> Layer A remains deferred. Layer B is partly approved: shared public Hub (M2) and separate company Hub (M3) are **planned**, not deployed by this document. See the [approved change request](CHANGE_REQUESTS.md).
> Only the capabilities explicitly marked deferred below remain outside the approved milestones.
>
> 🌐 Korean copy: **`FUTURE_KR.md`**. Edits here must be mirrored there (CLAUDE.md "Bilingual file convention").

---

## Why these are deferred
The original personal v1 avoided a governance gateway and org-scale infrastructure. The approved hub-commons change now brings **content delivery to multiple users** into M2/M3 without changing the six purposes. Layer A's tool-call governance and the remaining Layer B capabilities stay deferred (ADR-0001/0002/0003 are gateway-scoped). Intake authentication and publication checks are not a gateway.

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

## Layer B — public then company Hub

**Approved sequence:** personal → invite-only public Hub (M2) → separate company Hub (M3) → possible published standard later. The public Hub is effectively a closed group because an Operator must issue an invite token. It validates shared delivery before the company deployment; M3 starts only after company cloud and repository rights are verified. See the [approved change request](CHANGE_REQUESTS.md).

**M2 planned:** the public Hub's Worker authenticates intake and screens free text before D1 persistence; its one hourly, off-the-hour commons Actions workflow checks evidence, publishes the shared cache's canonical git copy, and delivers only `stable` by default (`trial` is opt-in). It never mediates external tool calls. The personal catalog remains isolated. Two distinct-lineage reports can recall a trial revision; stable recall requires Operator confirmation. GitHub Issue/PR free-text intake is not in M2.

**M3 planned:** the same Hub code is separately deployed for the company, with its own D1, release bucket, private commons, credentials, and company-issued invites. Public and company content do not fall back to each other. The two Hubs share a combined USD 20/month model-cost cap reserved before calls; Cloudflare and Actions are kept within free tiers.

| Capability still deferred | Reason | Revisit when |
|---------------------------|--------|--------------|
| Gateway-enforced multi-user governance and role allowlists | Hub access scopes authenticate content delivery, not external tool calls | An enforced tool-call policy is actually needed |
| Beginner-specific deployment beyond the approved Client/Hub path | The planned install and invite path has not been proven with beginners | Real users expose a gap |
| PII masking of regulated data | No regulated data may flow through the planned submission path | A separately approved regulated stage |
| Self-hosted Composio | Managed connection backends remain independent of the Hub | A regulated stage requires data residency |
| Publish the `services/`+`failures/` schema as a standard | First validate shared use of the derive-on-miss cache | After the company stage |

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
