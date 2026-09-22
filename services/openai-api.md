---
id: openai-api
display_name: "OpenAI API"
version_tag: "OpenAI API docs / Responses API (2026-08-12)"
last_verified: "2026-08-12"
endpoint:
  type: api
  server: "https://api.openai.com/v1"
human_steps:
  - "최초 OpenAI Platform 계정·프로젝트 접근과 결제 승인, 필요한 경우 프로젝트 API 키 1회 생성"
agent_can:
  - "기존 보안 저장소에서 OPENAI_API_KEY를 런타임에 불러오기"
  - "공식 SDK나 REST API로 모델 조회와 Responses API 호출하기"
  - "조직·프로젝트 범위와 데이터 보관 설정을 작업 목적에 맞게 적용하기"
status: active
distributable: true
---

## 한 줄 요약
OpenAI API는 공식 SDK와 REST API로 모델과 도구를 호출한다. 프로젝트 키는 서버 런타임이나 보안 저장소에서만 다룬다.

## 정답 절차
1. 기존 OpenAI 연결, `OPENAI_API_KEY`, 1Password 항목이 있는지 먼저 확인한다. 있으면 사용자에게 새 키를 요구하지 않는다.
2. 키가 없을 때만 사용자가 OpenAI Platform의 올바른 프로젝트에서 API 키를 만든다. 키는 보안 저장소에만 넣고 채팅에 붙여 넣지 않는다.
3. 에이전트는 `OPENAI_API_KEY`를 런타임에 주입한다. 브라우저나 모바일 앱처럼 공개되는 클라이언트에는 키를 넣지 않는다.
4. 새 텍스트·도구 작업은 공식 quickstart의 Responses API를 우선 검토한다. 기존 Chat Completions 코드라면 요구사항과 호환성을 확인한 뒤 유지한다.
5. 모델 이름을 기억에 의존하지 말고 `/v1/models`나 현재 모델 문서에서 계정에 허용된 id를 확인한다.
6. 여러 조직이나 프로젝트를 쓰면 요청 범위가 맞는지 확인한다. 민감한 데이터를 보내기 전에는 해당 엔드포인트의 현재 보관 정책과 프로젝트 데이터 제어를 확인한다.

## 하지 말 것
- API 키를 채팅, 코드, 저장소, 클라이언트 앱, 원문 로그에 남기지 않는다.
- ChatGPT 로그인과 OpenAI API 프로젝트 권한이 같다고 가정하지 않는다.
- 오래된 모델 이름이나 파라미터를 확인 없이 고정하지 않는다.

## 검증 범위
- 2026-08-12 공식 문서에서 API 키 인증, 환경변수 사용, Responses API quickstart, 모델 조회, 데이터 제어 문서를 확인했다.
- 실제 OpenAI API 키 생성과 유료 API 호출은 하지 않았다.

## 출처
- https://platform.openai.com/docs/quickstart/make-your-first-api-request
- https://platform.openai.com/docs/api-reference/authentication
- https://platform.openai.com/docs/api-reference/models
- https://platform.openai.com/docs/models/default-usage-policies-by-endpoint

## 관련 실패 기록
- 없음
