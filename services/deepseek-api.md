---
id: deepseek-api
display_name: "DeepSeek API"
version_tag: "DeepSeek API docs / V4 (2026-08-12)"
last_verified: "2026-08-12"
endpoint:
  type: api
  server: "https://api.deepseek.com"
human_steps:
  - "최초 DeepSeek Platform 계정·결제 승인, 필요한 경우 API 키 1회 생성"
agent_can:
  - "기존 보안 저장소에서 DEEPSEEK_API_KEY를 런타임에 불러오기"
  - "OpenAI 호환 SDK나 REST API로 현재 지원 모델 호출하기"
  - "401·402·429·5xx 오류를 구분해 안전하게 복구하기"
status: active
distributable: true
---

## 한 줄 요약
DeepSeek API는 OpenAI 호환 Chat Completions 경로를 제공한다. 현재 모델과 기준 주소를 공식 문서에서 확인한 뒤 호출한다.

## 정답 절차
1. 기존 `DEEPSEEK_API_KEY`나 1Password 항목이 있는지 먼저 확인한다. 있으면 새 키를 만들지 않는다.
2. 키가 없을 때만 사용자가 DeepSeek Platform에서 계정과 결제를 확인하고 키를 만든다. 키는 보안 저장소에만 넣는다.
3. 에이전트는 키를 런타임에 주입하고 기준 주소를 `https://api.deepseek.com`으로 설정한다.
4. 현재 API 문서나 모델 목록을 확인한 뒤 모델 id를 정한다. 2026-07-24에 지원 종료가 예고된 `deepseek-chat`, `deepseek-reasoner`를 새 설정에 고정하지 않는다.
5. OpenAI 호환 `/chat/completions` 형식을 사용하되, DeepSeek 전용 필드와 현재 지원 범위는 공식 API 스키마를 따른다.
6. `401`은 인증, `402`는 잔액, `429`는 사용량 제한, `500`과 `503`은 서버 문제로 나눠 처리한다. 서버 오류만 제한적으로 재시도한다.

## 하지 말 것
- API 키를 채팅, 코드, 저장소, 로그에 남기지 않는다.
- 과거 모델 별칭과 파라미터가 계속 유효하다고 가정하지 않는다.
- 인증 실패나 잔액 부족을 서버 오류처럼 반복 재시도하지 않는다.

## 검증 범위
- 2026-08-12 공식 문서에서 기준 주소, OpenAI 호환 Chat Completions, V4 모델 전환, 오류 코드를 확인했다.
- 실제 DeepSeek 계정 연결과 유료 API 호출은 하지 않았다.

## 출처
- https://api-docs.deepseek.com/api/deepseek-api
- https://api-docs.deepseek.com/api/create-chat-completion
- https://api-docs.deepseek.com/updates
- https://api-docs.deepseek.com/quick_start/error_codes/

## 관련 실패 기록
- 없음
