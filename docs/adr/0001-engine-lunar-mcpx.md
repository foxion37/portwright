# Engine = Lunar MCPX (not Lasso) for personal v1

> **Status: DEFERRED to FUTURE.** v1 dropped the gateway entirely (see ARCHITECTURE.md D1, FUTURE.md Layer A). This ADR applies only if/when the governance gateway is revived. Kept as the engine-selection record for that point.

For personal v1 we use **Lunar MCPX** as the gateway engine, overriding the earlier docs that named Lasso the "1순위" choice. Lunar fits the v1 drivers — tool-level policy enforcement (③), an immutable audit log (④), and broad multi-client support including Cursor/Claude Code/VSCode/Copilot (⑥) — with lower latency. Lasso's strengths (PII redaction, prompt-injection defense, compliance) are regulated-stage value and carry ~100-250ms of security-scan latency we don't need yet, so Lasso moves to `FUTURE.md`.

## Considered Options
- **Lunar MCPX** (chosen) — multi-client + tool-level policy + immutable audit, lighter.
- **Lasso** — security-first (PII/injection); deferred to the regulated stage.
- **ContextForge / Bifrost / Docker MCP Gateway / Obot** — viable, not a better fit for the v1 drivers.

## Consequences
- Building config/integration on Lunar is some lock-in; switching engines later means hand-rewriting the engine config (no compiler — see ADR-0002 context / `FUTURE.md`).
