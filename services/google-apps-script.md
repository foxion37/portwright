---
id: google-apps-script
display_name: "Google Apps Script (clasp)"
version_tag: "clasp 3.3.0"
last_verified: "2026-09-22"
endpoint:
  type: cli
  server: "clasp"
human_steps:
  - "계정별 1회: https://script.google.com/home/usersettings 에서 Apps Script API를 켠다"
  - "계정별 1회: `clasp login` 브라우저 OAuth 동의 클릭"
agent_can:
  - "인증 계정 확인, 스크립트 목록 조회, 클론, push/pull"
  - "버전 생성, 배포 생성/갱신/삭제, 배포 목록 조회"
  - "함수 실행(`run-function`), 로그 확인(`tail-logs`), API 활성화 조회"
status: active
distributable: true
freshness_evidence:
  url: https://raw.githubusercontent.com/google/clasp/master/README.md
---

## 한 줄 요약
Apps Script는 웹 편집기 대신 `clasp` 3.x CLI로 다룬다. 계정당 API 토글과 `clasp login` 1회만 사람 몫이고, 이후 클론·push·버전·배포는 에이전트가 직접 한다.

공식 문서 갱신 대상은 google/clasp README의 명령 목록과 2.x→3.x 변경표다. `developers.google.com/apps-script/guides/clasp` 페이지는 2.x 명령 이름(`clasp open`, `clasp version`)을 그대로 두고 있어 3.x 기준으로는 신뢰하지 않는다.

## 정답 절차
1. `clasp --version`으로 메이저 버전을 먼저 확인한다. 3.x와 2.x는 명령 이름이 다르다.
2. `clasp show-authorized-user`로 로그인 계정을 확인한다(2.x의 `clasp login --status`는 없다). 계정이 여럿이면 모든 명령에 `--user <name>`을 붙인다.
3. 인증이 없을 때만 `clasp login`을 사람에게 요청한다. 인증 파일은 전역 `~/.clasprc.json` 또는 프로젝트 옆 `.clasprc.json`이고, `-A/--auth`나 `clasp_config_auth`로 경로를 지정한다.
4. 기존 스크립트는 `clasp clone-script <scriptId|URL> [--rootDir <dir>]`, 새 스크립트는 `clasp create-script --title <제목> [--type standalone|sheets|docs|slides|forms|webapp|api] [--parentId <드라이브 파일 id>]`로 만든다. 둘 다 `.clasp.json`과 `appsscript.json`을 쓴다.
5. 올리기 전 `clasp show-file-status --json`으로 전송 대상 파일을 확인한다. `.claspignore`는 `rootDir` 기준이고 없으면 매니페스트와 `.gs/.js/.ts/.html`만 올라간다.
6. `clasp push`로 올리고, 릴리스가 필요하면 `clasp create-version [설명]` → `clasp create-deployment --versionNumber <n> --description <설명>`, 기존 배포 갱신은 `clasp update-deployment <deploymentId>`를 쓴다(2.x의 `deploy -i <id>`는 없다).
7. 실행·로그는 `clasp run-function [함수명]`, `clasp tail-logs [--watch]`를 쓴다. 이 두 계열은 `.clasp.json`에 `projectId`가 있어야 한다.
8. MCP로 붙일 때는 `clasp start-mcp-server`(별칭 `clasp mcp`)를 쓴다. 이때도 API 토글과 `clasp login`은 먼저 끝나 있어야 한다.
9. 실패하면 먼저 `failures/` 폴더에서 같은 서비스 기록을 확인한다.

## 하지 말 것
- 코드를 웹 편집기에 붙여넣으라고 시키지 않는다. `clasp push`가 되는데 사람 손을 쓰는 건 이 프로젝트가 없애려는 패턴이다.
- 2.x 명령 이름을 그대로 쓰지 않는다. `open`→`open-script`, `apis enable`→`enable-api`, `logs --open`→`open-logs`, `deploy -i`→`update-deployment`이고 `logs --setup`·`settings`는 사라졌다.
- TypeScript 소스를 `clasp push`가 트랜스파일해 준다고 가정하지 않는다. 3.x는 TS 변환을 빼서 번들러로 먼저 빌드해야 한다.
- 기본 OAuth 클라이언트로 되는 작업에 `--creds`나 자체 GCP 프로젝트, 토큰 발급을 요구하지 않는다. 조직 정책으로 서드파티 앱이 막힌 경우만 자체 클라이언트를 만든다.
- 서비스 계정과 `--adc`를 자동화 경로로 제안하지 않는다. upstream이 EXPERIMENTAL/NOT WORKING으로 표시하고, 서비스 계정은 스크립트를 소유할 수 없다.
- `~/.clasprc.json`이나 `--creds` 파일 내용을 출력하거나 기록하지 않는다.

## 관련 실패 기록
- 없음
