---
date: 2026-05-31
service: composio
service_version: "composio 1.0.0-rc2 SDK / Tool Router"
status: active
distributable: true
---

## 무엇을 시도했나
Composio로 앱(Vercel/GitHub/Notion 등) 실제 작업을 하려 했고, `COMPOSIO_SEARCH_TOOLS`가 "restricted in this environment"을 뱉자 **"이 Claude Code 환경이 Composio 앱 실행을 차단한다"**고 결론짓고 `services/composio.md`에 그렇게 캐시했다.

## 어떤 에러가 났나
`COMPOSIO_MANAGE_CONNECTIONS(["github","vercel"])` →
`[Session Restriction] Toolkit 'github' is not allowed for this session. You cannot execute tools from this toolkit.`

## 진짜 원인 (오진이었음)
환경 차단도, 계정/플랜 차단도 **아니었다.** Tool Router **세션을 `c.create(user_id=..., toolkits=["composio"])` — 메타 툴킷 하나만으로 발급**한 게 원인. 앱 툴킷(vercel/notion/…)을 세션에 안 넣어서 "이 세션엔 허용 안 됨"으로 막힌 것. `SEARCH_TOOLS`의 "in this environment"는 **Claude Code 샌드박스가 아니라 그 Tool Router 세션**을 가리켰는데 그걸 "환경"으로 오독했다.

**검증(실측):** `toolkits=["composio","vercel","notion"]`로 새 세션 발급 → `session.toolkits()`가 vercel·notion을 `allowed`(connection `is_active=False`, 즉 미연결일 뿐)로 반환. Session Restriction 사라짐 → 자기설정이 원인임을 확정.

## 고친 방법 (다음엔 이대로)
- 쓰려는 앱을 **세션 발급 시 toolkits에 포함**: `c.create(user_id=..., toolkits=["composio","vercel","notion",...])`. 그 뒤 MCP 재등록(출력 억제).
- 앱을 실제 **실행**하려면 그 앱은 여전히 Composio OAuth 1회 필요(`is_active=False`→true). "allowed"와 "connected"는 다르다.
- **메타 교훈(이게 핵심):** *원인을 모르면 "환경이 막는다" 같은 추측을 캐시하지 마라.* 추측 근본원인을 캐시하면 다음 에이전트가 그걸 믿고 멀쩡한 백엔드를 버린다 — 캐시(purpose B)가 독이 된다. 근본원인은 **재현·확인 후** 기록한다.
- → `services/composio.md`의 "환경 차단" 서술을 이 검증본으로 교체함.
