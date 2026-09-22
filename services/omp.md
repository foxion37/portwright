---
id: omp
display_name: "Oh My Pi"
version_tag: "OMP 18.1.4"
last_verified: "2026-09-03"
endpoint:
  type: cli
  server: "omp"
human_steps:
  - "없음 (플러그인 설치와 검증을 에이전트가 수행한다)"
agent_can:
  - "npm 플러그인 미리보기, 설치, 목록 확인, 상태 검사, 제거"
status: active
distributable: true
---

## 한 줄 요약
OMP 플러그인은 `omp install`로 사용자 영역에 설치하고 새 OMP 세션에서 실제 활성화를 확인한다.

## 정답 절차
1. 설치 대상 도구의 Portwright Procedure와 active Lesson을 먼저 확인한다.
2. `omp install <npm-spec> --dry-run --json`으로 패키지 인식 결과를 확인한다.
3. `omp install <npm-spec> --json`으로 설치한다. 기본값은 사용자 범위이며, 프로젝트 설치를 명시적으로 요구받은 경우에만 `--scope=project`를 쓴다.
4. `omp plugin list --json`에서 버전, manifest의 extensions와 skills, `enabled: true`를 확인한다.
5. `omp plugin doctor --json`에서 해당 플러그인의 상태가 `ok`인지 확인한다.
6. 새 OMP 세션을 PTY로 시작해 플러그인의 시작 메시지, 상태 표시, 등록 명령을 실제로 실행한다.
7. 제거는 `omp plugin uninstall <package-name> --json`을 사용하고 `omp plugin list --json`에서 항목이 사라졌는지 확인한다.

## 하지 말 것
- `~/.pi/agent` 설치 결과를 OMP 설치로 간주하지 않는다.
- 현재 세션의 정적 스킬 목록만 보고 새 플러그인의 활성화를 주장하지 않는다.

## 관련 실패 기록
- `failures/2026-07-23-omp-claude-tools-auto-load-stdin-block.md`
