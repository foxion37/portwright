---
date: <YYYY-MM-DD, Failure가 일어난 날>
service: <서비스 id, 예: vercel>
service_version: <그때의 Service/API/MCP/CLI 버전, 버전이 바뀌면 이 Lesson을 의심한다>
status: active        # active | stale(서비스 업데이트로 더는 안 맞을 수 있음)
profile_id:           # 선택: 이 교훈이 적용되는 Profile id (없으면 모든 Profile)
distributable: true   # true면 git으로 배포, false면 failures/_private/ (gitignore)로 이동
---

## 무엇을 시도했나
<에이전트가 한 행동>

## 어떤 에러가 났나
<오류 메시지 요약, 민감정보 제거>

## 진짜 원인
<확인된 root cause, 확인 전 draft에는 unconfirmed>

## 고친 방법 (다음엔 이대로)
<수정된 절차. 관련 Procedure에도 Lesson 경로를 반영한다>
