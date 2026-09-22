---
# === 기계가 읽는 부분 (위쪽 ---사이) ===
id: <서비스 영문 소문자, 예: vercel>
display_name: <표시 이름>
version_tag: <확인한 공식 문서/API/MCP/CLI 버전 또는 날짜>
last_verified: <YYYY-MM-DD, 사람 몫 경로를 현재 공식 문서에서 마지막으로 확인한 날>
endpoint:                # Agent가 이 Service에 닿는 전송 방식 (CONTEXT.md: Endpoint)
  type: mcp              # api | cli | mcp | oauth 중 하나
  server: <MCP 서버, API, OAuth 앱 또는 CLI 이름>
human_steps:           # 사람만 할 수 있는, 불가피한 단계만 적는다 (최소로!)
  - <예: 최초 1회 OAuth 동의 클릭>
agent_can:             # 연결만 되면 에이전트가 알아서 하는 것
  - <예: 프로젝트 생성, 배포, 환경변수 설정>
status: active         # active | stale(서비스가 바뀌어 재확인 필요)
profile_ids:           # 선택: 이 절차가 적용되는 Profile id 목록 (없으면 모든 Profile)
  - <예: ccc>
distributable: true    # true면 git으로 배포, false면 services/_private/ (gitignore)로 이동
# 선택: 공개 공식 문서의 최종 HTTPS URL. 인증·쿼리·리디렉션이 필요한 URL은 제외한다.
# freshness_evidence:
#   url: https://docs.example.com/tool
---

## 한 줄 요약
<이 서비스가 뭐고, 에이전트가 어떻게 쓰면 되는지 한 문장으로>

## 정답 절차
1. ...
2. ...
3. 실패하면 먼저 `failures/` 폴더에서 같은 서비스 기록을 확인한다.

## 하지 말 것
- <에이전트가 흔히 시키는 불필요한 수동작업을 여기에 금지로 적는다>

## 관련 실패 기록
- failures/<파일명>.md
