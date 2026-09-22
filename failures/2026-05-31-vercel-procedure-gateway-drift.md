---
date: 2026-05-31
service: vercel
service_version: "Vercel MCP 2026-05 / portwright v1 (no gateway)"
status: active
distributable: true
---

## 무엇을 시도했나
v1-done 데모로 Vercel 배포 루프를 돌리기 전, verify-before-instruct 규칙대로 캐시된 `services/vercel.md`를 먼저 읽고 신선도를 확인했다.

## 어떤 에러가 났나
런타임 에러가 아니라 **절차 드리프트**(stale procedure). `vercel.md`가 v1 아키텍처와 모순되는 표현을 담고 있었다:
1. "Vercel MCP가 **게이트웨이**에 연결됐는지 확인" — v1엔 게이트웨이가 없다 (FUTURE로 연기된 설계).
2. "인증은 **게이트웨이**가 보유한다" — 실제로는 네이티브 Vercel MCP(OAuth)가 보유한다.
3. "(배포는 **CONFIRM 단계**)" — CONFIRM 티어는 게이트웨이 정책 기능 → FUTURE. v1엔 없는 개념.

## 진짜 원인
초기 절차가 **게이트웨이 기반 설계(구 비전)를 전제로 작성**됐는데, 그 뒤 v1이 "게이트웨이 없음 / Composio + 절차 캐시"로 재정렬(ARCHITECTURE.md, HANDOFF.md)되면서 절차 문서가 따라오지 못함. 도구 UI가 바뀐 게 아니라 **우리 자신의 아키텍처 결정이 바뀌어 절차가 stale**해진 케이스 — verify-before-instruct가 외부 도구 변경뿐 아니라 내부 설계 변경에도 걸린다는 증거.

## 고친 방법 (다음엔 이대로)
- 게이트웨이/CONFIRM 표현 제거 → "네이티브 Vercel MCP 직결, OAuth가 인증 보유"로 교정.
- `deploy_to_vercel`은 **인자 없이 현재 cwd 프로젝트**를 배포한다는 실제 동작을 명시 + cwd 링크 확인 경고 추가 (운영 사이트 오배포 방지).
- `last_verified`는 이 세션에서 **실제 실행한 read 호출**(list_teams/list_projects, 토큰 재요청 0회)만 근거로 갱신. 배포 자체는 미실행이라 본문에 명시.
- → `services/vercel.md`를 검증본으로 갱신함.
