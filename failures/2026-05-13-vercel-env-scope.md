---
date: 2026-05-13
service: vercel
service_version: "MCP 2026-04"
status: active
distributable: true
---

## 무엇을 시도했나
에이전트가 환경변수를 Production 스코프에만 넣고 곧바로 배포를 실행했다.

## 어떤 에러가 났나
빌드는 통과했지만, Preview 배포에서 환경변수를 못 찾아 런타임 에러가 났다.

## 진짜 원인
Vercel은 환경변수를 Production / Preview / Development 세 스코프로 나눈다.
에이전트가 Production에만 설정해서 Preview 쪽이 비어 있었다. (표면 에러는 "변수 없음"이지만 원인은 스코프 누락)

## 고친 방법 (다음엔 이대로)
환경변수는 세 스코프 모두에 설정하거나, 대상 스코프를 명시적으로 확인한 뒤 넣는다.
→ services/vercel.md 의 "정답 절차"에 "환경변수는 스코프 확인 필수" 한 줄을 반영했다.
