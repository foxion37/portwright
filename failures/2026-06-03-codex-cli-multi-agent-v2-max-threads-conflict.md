---
date: 2026-06-03
service: codex-cli
service_version: codex-cli 0.131.0
status: active
distributable: true
---

# Codex CLI multi-agent v2 max_threads conflict

## 무엇을 시도했나

Codex CLI/App startup failure screenshot and `codex exec --skip-git-repo-check 'Reply exactly: OK'` smoke test failure를 조사했다.

## 어떤 에러가 났나

`Error: agents.max_threads cannot be set when multi_agent_v2 is enabled`

Screenshot OCR also showed: `failed to resolve feature override precedence: agents.max_threads cannot be set when multi_agent_v2 is enabled`.

## 진짜 원인

`~/.codex/config.toml` had both legacy `[agents] max_threads = 6` and `[features.multi_agent_v2] enabled = true`. Codex CLI 0.131.0 rejects that combination during config/feature override resolution before any model call starts.

## 고친 방법 (다음엔 이대로)

Back up `~/.codex/config.toml`, then remove or comment out only `agents.max_threads` while leaving `features.multi_agent_v2.enabled = true` and other agent settings intact.

Verified on 2026-06-03 with a Codex smoke test: `codex exec --skip-git-repo-check 'Reply exactly: OK'` reached the model and returned `OK`.
