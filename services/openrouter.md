---
id: openrouter
display_name: "OpenRouter API"
version_tag: "OpenRouter API v1 docs (2026-08-12)"
last_verified: "2026-08-12"
endpoint:
  type: api
  server: "https://openrouter.ai/api/v1"
human_steps:
  - "최초 계정 로그인과 OAuth PKCE 동의 또는 결제 승인. 직접 API 키를 쓰는 경우 키는 보안 저장소에만 보관한다."
agent_can:
  - "기존 연결이나 보안 저장소에서 OPENROUTER_API_KEY를 런타임에 불러오기"
  - "현재 모델 목록을 확인하고 OpenAI 호환 API 호출하기"
  - "429·503 응답의 Retry-After를 지켜 재시도하기"
status: active
distributable: true
---

## 한 줄 요약
OpenRouter는 여러 모델 제공자를 하나의 OpenAI 호환 API로 쓰는 서비스다. 기존 연결을 먼저 찾고, 없으면 OAuth PKCE 동의 한 번으로 연결한다.

## 정답 절차
1. 기존 OpenRouter 연결, `OPENROUTER_API_KEY`, 1Password 항목이 있는지 먼저 확인한다.
2. 새 연결이 필요하면 OAuth PKCE를 우선한다. 사용자는 로그인과 동의만 맡고, 에이전트가 인증 코드 교환과 런타임 설정을 처리한다.
3. 직접 API 키 방식이 꼭 필요하면 사용자가 OpenRouter에서 키를 만든 뒤 1Password 같은 보안 저장소에 바로 넣는다. 키를 채팅에 붙여 넣지 않는다.
4. 에이전트는 키를 런타임에만 주입하고 `https://openrouter.ai/api/v1`을 기준 주소로 쓴다.
5. 호출 전 `/models`에서 현재 모델 id를 확인한다. 오래된 모델 이름을 기억에 의존해 고정하지 않는다.
6. 기본 호환 경로는 Chat Completions다. Responses API는 현재 Beta이고 무상태이므로 기능이 꼭 필요할 때만 선택한다.
7. `429`나 `503`에는 `Retry-After`가 있으면 그 값을 따른다. 무작정 빠르게 반복 호출하지 않는다.

## 하지 말 것
- API 키를 채팅, 문서, 저장소, 명령 출력에 남기지 않는다.
- 사용자가 이미 연결한 키를 다시 만들게 하지 않는다.
- OpenRouter Responses API가 OpenAI Responses API와 모든 상태 동작까지 같다고 가정하지 않는다.

## 검증 범위
- 2026-08-12 공식 문서에서 인증, v1 기준 주소, OpenAI 호환 방식, Responses API Beta의 무상태 특성, `Retry-After` 처리를 확인했다.
- 실제 계정 연결과 유료 API 호출은 하지 않았다.

## 출처
- https://openrouter.ai/docs/quickstart
- https://openrouter.ai/docs/api/reference/responses/overview
- https://openrouter.ai/docs/api/reference/errors-and-debugging
- https://openrouter.ai/docs/api/api-reference/o-auth/exchange-auth-code-for-api-key

## 관련 실패 기록
- 없음
