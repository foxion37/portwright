---
id: grok-api
display_name: "xAI Grok API"
version_tag: "xAI Inference REST API docs (2026-08-12)"
last_verified: "2026-08-12"
endpoint:
  type: api
  server: "https://api.x.ai/v1"
human_steps:
  - "최초 xAI 계정·팀 접근, 약관·결제 승인, 필요한 경우 최소 권한 API 키 1회 생성"
agent_can:
  - "기존 보안 저장소에서 XAI_API_KEY를 런타임에 불러오기"
  - "현재 모델 목록과 키 권한을 확인하고 OpenAI 호환 REST API 호출하기"
  - "응답 코드에 따라 인증, 권한, 사용량 제한 문제를 구분하기"
status: active
distributable: true
---

## 한 줄 요약
xAI의 Grok 모델을 OpenAI 호환 REST API로 호출한다. 추론용 키는 최소 권한으로 만들고 런타임에만 넣는다.

## 정답 절차
1. 기존 `XAI_API_KEY`나 1Password 항목이 있는지 먼저 확인한다. 이미 있으면 새 키를 요구하지 않는다.
2. 키가 없을 때만 사용자가 xAI Console에서 계정과 결제를 확인하고 추론에 필요한 최소 권한 키를 만든다. 키는 보안 저장소에만 넣는다.
3. 에이전트는 `Authorization: Bearer` 방식으로 키를 런타임에 주입하고 `https://api.x.ai/v1` 경로를 쓴다.
4. 모델을 고정하기 전에 `/v1/models`에서 현재 계정에 허용된 모델을 확인한다.
5. 추론 호출에는 일반 API 키를 쓴다. 팀의 키와 권한을 관리하는 Management API 키를 모델 호출용으로 쓰지 않는다.
6. `401`은 키, `403`은 권한, `429`는 사용량 제한으로 나눠 확인한다. 오류 응답에 키나 요청 본문 전체를 남기지 않는다.

## 하지 말 것
- API 키나 Management API 키를 채팅, 코드, 저장소, 로그에 남기지 않는다.
- 계정에 허용된 모델을 확인하지 않고 기억 속 모델 이름을 고정하지 않는다.
- 웹 브라우저처럼 공개되는 클라이언트 코드에 키를 넣지 않는다.

## 검증 범위
- 2026-08-12 공식 문서에서 REST 기준 주소, Bearer 인증, OpenAI 호환성, 모델 목록, 키 권한 방식을 확인했다.
- 실제 xAI 계정 연결과 유료 API 호출은 하지 않았다.

## 출처
- https://docs.x.ai/developers/rest-api-reference/inference
- https://docs.x.ai/developers/rest-api-reference/management/auth

## 관련 실패 기록
- 없음
