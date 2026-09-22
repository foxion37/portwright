---
date: 2026-05-31
service: composio
service_version: "composio 1.0.0-rc2 SDK / claude 2.1.157 / docs 2026-05"
status: active
distributable: true
---

## 무엇을 시도했나
Composio를 Claude Code에 연결(MCP). 공식 toolkit 문서의 절차를 따랐다.

## 어떤 에러가 났나 (3연속 드리프트)
1. 문서: `pip install composio-core` → 설치한 `Composio` 클래스에 `.create()` 없음 (AttributeError). legacy 패키지였음.
2. 신 패키지 `composio`(1.0.0-rc2)엔 `c.create(user_id, toolkits).mcp.url` 있음 → 발급 성공.
3. 문서: `claude mcp add ... --headers` → 실제 플래그는 `--header`(단수).
4. **키 노출:** `claude mcp add`가 성공 출력에 헤더를 JSON `"X-API-Key": "ak_..."`로 되울림. 내 마스킹 정규식은 `X-API-Key:`(콜론 형태)만 잡아 JSON 형태를 놓침 → 생키가 대화 기록에 노출됨.

## 진짜 원인
- 문서가 구버전(composio-core)과 신버전(composio) API를 섞어 적은 썩은 문서.
- `claude mcp add`는 등록 확인용으로 헤더를 그대로 echo함 → 비밀이 stdout에 나옴. 마스킹은 한 가지 형태만 막아선 안 됨.

## 고친 방법 (다음엔 이대로)
- 패키지는 **`composio`**(신, `c.create().mcp.url`), `composio-core` 아님.
- 플래그는 **`--header`**(단수), `--scope user`로 글로벌.
- **`claude mcp add` 출력을 절대 raw로 찍지 말 것** → `>/dev/null`로 버리거나, URL+모든 헤더 값 형태를 다 마스킹. echo 안 하는 게 안전.
- 노출된 키는 **즉시 교체**(dashboard regenerate → 1Password 갱신 → 재등록).
- → `services/composio.md`의 정답 절차를 이 검증본으로 갱신함.
