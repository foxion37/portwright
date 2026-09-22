---
status: informational (gateway deferred to FUTURE; findings preserved for revival)
---

# Lunar MCPX spike findings (doc verification)

> **Outcome:** these findings (esp. "no CONFIRM tier" + "audit likely paid") were part of why v1 dropped the gateway entirely. The gateway is now FUTURE (Layer A). This record stands for whoever revives it. Live Docker checks below were not run (no longer on the v1 path).

Verified the four Step-0 questions against Lunar's own docs/GitHub (MIT, `TheLunarCompany/lunar`) before standing up Docker. Two assumptions behind ADR-0001/0002 do **not** hold as written.

## Answers
1. **Policy: default tier + tool patterns?** PARTIAL.
   - ✅ Deny-by-default works: OSS YAML supports `permissions: base: "block"`.
   - ✅ Per-tool + wildcards work: `toolGroups` allow individual tools and `service: "*"`.
   - ❌ **No CONFIRM tier.** MCPX policy is **allow/block only**. There is no "require approval / human-in-the-loop / confirm once" action at the gateway. The AUTO/CONFIRM/FORBIDDEN three-tier model collapses to **two tiers (allow = AUTO, block = FORBIDDEN)** at the gateway.
2. **Front Composio?** ✅ LIKELY. MCPX aggregates/launches upstream MCP servers; Composio exposes an MCP endpoint, so it mounts as a service. (Confirm on a live instance.)
3. **Audit log readable by a script?** ⚠️ UNCONFIRMED / likely gated. The OSS access-control post lists "per-consumer audit logs and tracing" under **roadmap ("What's Next")**. Rich queryable/exportable audit appears tied to the **paid Control Plane**, not OSS core. This threatens the D7 auto-draft `failures/` pipeline (the differentiator).
4. **Free OSS tier covers it?** ✅ policy/ACL (MIT, free, personal use). ⚠️ exportable audit log likely Control-Plane (paid).

## What this breaks (decisions now needed)
- **CONFIRM has no home at the gateway.** Options: (a) move CONFIRM to the Client (Claude Code already prompts on destructive actions — but Cursor/ChatGPT differ, weakening driver ⑥ "consistent across clients"); (b) drop CONFIRM, run gateway as allow/block only (`base: block` + explicit allow list = FORBIDDEN-default, which the user did NOT pick in the earlier grill); (c) re-pick an engine that supports interactive approval. → ADR-0002 and `policy/tiers.yaml`'s three-tier model must be reworked.
- **Audit-driven failure pipeline (the moat) may require the paid Control Plane.** Verify on a live OSS instance before banking on D7; otherwise capture failures another way.

## Live checks still owed (Docker)
- Does the OSS build write a script-readable audit log (file/API), or dashboard-only?
- Does Composio actually mount cleanly as an upstream service?

## Sources
- ACL (OSS YAML, base:block + wildcards, allow/block only): https://www.lunar.dev/post/mcp-gateway-access-controls-defining-permissions-for-llm-agents
- ACL docs (allow/block, agent-centric, no confirm): https://docs.lunar.dev/mcpx/access_control_list/
- License/OSS scope: https://github.com/TheLunarCompany/lunar
