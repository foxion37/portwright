# Autonomy default tier = CONFIRM (deny-by-default)

> **Status: DEFERRED to FUTURE + needs rework.** v1 has no gateway, so no policy enforcement (ARCHITECTURE.md D1). Also ADR-0003 found the candidate engine is allow/block only — there is no CONFIRM tier — so this decision must be reworked before any gateway revival. Kept as the rationale record.

A tool call that matches none of the `auto`/`confirm`/`forbidden` patterns in `policy/tiers.yaml` is treated as **CONFIRM**, not AUTO. MCP tool names aren't standardized, so a destructive tool named off-pattern (e.g. `remove_project` doesn't match `delete_*`) would otherwise pass silently. Fail-closed means nothing unrecognized runs without a human seeing it once.

## Considered Options
- **CONFIRM** (chosen) — unmatched calls prompt once. Catches off-pattern destructive tools without locking out new read tools.
- **FORBIDDEN** — safest, but blocks every unclassified tool (including harmless reads) until added to `tiers.yaml`; too much friction for solo operation.
- **AUTO** (fail-open) — rejected: a misnamed destructive tool would run with no gate.

## Dependency (verify in the Lunar spike)
This assumes the engine can express a **default tier + tool-name pattern matching**. If Lunar MCPX only supports server-level allow/deny, fall back to an **explicit tool allowlist** (deny everything not listed) to achieve the same deny-by-default posture. The Lunar spike (HANDOFF Step 0) verifies this before build.
