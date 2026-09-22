---
id: ponytail
display_name: "Ponytail"
version_tag: "Ponytail 4.9.0; verified with OMP 18.1.4"
last_verified: "2026-09-03"
endpoint:
  type: cli
  server: "omp"
human_steps:
  - "없음 (설치와 검증을 에이전트가 수행한다)"
agent_can:
  - "고정 버전 설치"
  - "설치 목록 확인"
  - "확장 테스트"
  - "제거"
status: active
distributable: true
---

## 한 줄 요약
Ponytail은 앱 의존성이 아니라 OMP 에이전트의 코드 최소화 규칙과 검토 명령을 추가하는 사용자 플러그인이다.

## 정답 절차
1. 공식 저장소 `https://github.com/DietrichGebert/ponytail`에서 릴리스 `v4.9.0`, MIT 라이선스, npm provenance, `preinstall`·`install`·`postinstall`·`prepare` 스크립트 없음이 유지되는지 확인한다. 하나라도 다르거나 새 버전을 쓰려면 다시 검토하며, 그전에는 `4.9.0`을 유지한다.
2. `omp --version`을 확인한다. 이 절차는 OMP 18.1.4에서 검증했다. 이후 `omp install @dietrichgebert/ponytail@4.9.0 --json`을 실행한다. 기본 사용자 범위를 사용해야 프로젝트 파일을 건드리지 않는다.
3. `omp plugin list --json`에서 `@dietrichgebert/ponytail`의 버전이 `4.9.0`, `enabled`가 `true`, manifest의 extension과 skills가 각각 `./pi-extension/index.js`, `./skills`인지 확인한다.
4. `omp plugin doctor --json`에서 `plugin:@dietrichgebert/ponytail`이 `ok`인지 확인하고, `npm test --prefix <설치 경로>/pi-extension`을 실행해 실패가 0건인지 확인한다.
5. 새 OMP 세션을 시작해 `Ponytail loaded: full`과 상태 표시줄의 `ponytail: FULL`을 확인한다. `/ponytail status`를 입력하면 현재 모드가 표시되어야 한다.
6. 앱의 `package.json`, lockfile, CI에는 추가하지 않는다. Ponytail은 빌드 산출물과 무관한 에이전트 확장이다.
7. 설정과 상태 파일까지 지우려면 먼저 `node <설치 경로>/scripts/uninstall.js`를 실행한 뒤 `omp plugin uninstall @dietrichgebert/ponytail --json`을 쓴다. 마지막으로 `omp plugin list --json`에서 항목이 사라졌는지 확인한다.

## 하지 말 것
- 검토하지 않은 Git main을 설치하지 않는다. 검증한 npm 릴리스 또는 커밋을 고정한다.
- Ponytail을 제품 런타임 의존성이나 CI 빌드 단계에 넣지 않는다.

## 관련 실패 기록
- 없음
