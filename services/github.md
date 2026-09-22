---
id: github
display_name: "GitHub"
version_tag: "GitHub CLI 2.97.0"
last_verified: "2026-08-31"
endpoint:
  type: cli
  server: "gh"
human_steps:
  - "보호 브랜치에서 사람 승인이 필수인 경우 PR 검토와 승인"
agent_can:
  - "활성 계정과 저장소 상태 확인"
  - "브랜치 push, PR 생성, 상태 조회, 허용된 merge 실행"
status: active
distributable: true
freshness_evidence:
  url: https://cli.github.com/manual/gh_auth_switch
---

## 한 줄 요약
`gh` 활성 계정과 Git 전송 계정을 분리해서 확인한 뒤, 저장소를 명시해 PR을 만들고 원격 상태를 API로 검증한다.

공식 문서 갱신 대상은 계정 전환(`gh auth switch`) 설명이다. 다운로드 성공은 PR·merge 등 이 노트의 모든 절차가 재검증되었다는 뜻이 아니다.

## 정답 절차
1. `gh auth status`와 `gh api user --jq .login`으로 활성 GitHub 계정을 확인한다.
2. SSH 원격이면 SSH 인증 계정도 별도로 확인한다. 계정이 다르면 저장소 로컬 설정에만 HTTPS와 `gh auth git-credential`을 적용한다.
3. 변경 범위와 검증 결과를 확인한 뒤 `git push -u origin <branch>`로 올린다.
4. `gh pr create --repo <owner>/<repo> --base <base> --head <branch> --title <title> --body-file <file>`로 PR을 만든다.
5. `gh pr view <number> --repo <owner>/<repo> --json state,url,headRefName,baseRefName`으로 생성 결과를 확인한다.
6. linked worktree에서 merge할 때는 중립 디렉터리에서 `--repo`를 명시한다. merge 오류 뒤에는 재시도 전에 원격 PR 상태를 먼저 확인한다.

## 하지 말 것
- `gh` 활성 계정만 보고 SSH push 작성자까지 같다고 가정하지 않는다.
- linked worktree 안에서 `gh pr merge --delete-branch`를 실행해 로컬 main checkout 충돌을 만들지 않는다.
- `gh api` 조회에 `-f`를 붙여 의도치 않은 POST 요청을 만들지 않는다.
- secret이나 token 값을 출력하거나 파일에 남기지 않는다.

## 관련 실패 기록
- `failures/2026-06-03-github-graphql-array-variable.md`
- `failures/2026-06-19-github-api-deployments-query.md`
- `failures/2026-07-24-github-active-account-vs-ssh-key.md`
- `failures/2026-07-27-github-pr-merge-worktree-main-conflict.md`
