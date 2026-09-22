# 유지보수 — portwright v1 (혼자)

이 레이어는 두 가지로 산다: **(1) 우리는 내용물(절차 + 교훈)만 관리한다. (2) 절차를 최신으로 유지해 에이전트가 낡은 경로를 지시하지 않게 한다.**

> 🌐 영문 사본: **`MAINTENANCE.md`**. 여기 내용을 고치면 그쪽도 같이 고쳐야 합니다 (CLAUDE.md "이중 언어 파일 규칙").

## 역할 분담
- 연결 백엔드(Composio든 네이티브 MCP/CLI든) = 자격증명이 사는 곳. 나는 실행 가능한 백엔드로 도구를 한 번 연결.
- **내용물(이 레포)** = 내 것: `services/` 절차 + `failures/` 교훈. 작고 가벼움.
- **띄울 게이트웨이 없음.** 지킬 상주 서비스 없음. (게이트웨이는 `FUTURE.md`.)

## 루틴 — 사람 단계는 신선하게, 에이전트 호출은 캐시로
**사람 단계**(에이전트가 사용자에게 넘기는 단계)는 지시 직전 현재 문서에서 경로를 새로 도출한다. **에이전트 자기 호출**은 캐시된 절차/교훈을 먼저 읽어 재실패를 막는다 — 신선도 면제가 아니라 캐시 제공 대상. 읽을 감사로그도, 살려둘 서비스도 없다.
1. **지시 직전 도출.** 에이전트가 사용자에게 수동 단계를 넘기기 직전에 현재 공식 문서에서 경로를 다시 확인한다. v1에는 자동 추적할 감사로그가 없다.
2. **행동 전 확인.** 도구를 호출하기 전에 `bin/portwright preflight <service-id>`를 실행해 현재 Procedure와 active Lesson을 읽는다. 사람 몫을 안내할 때는 `--intent instruct`를 붙인다.
3. **잡고 고치기.** 단계나 도구 호출이 실패하면 root cause를 확인하고 `bin/portwright memory draft lesson <service-id> <slug>`로 Lesson draft를 만든다. `memory review`를 통과한 draft만 정식 Memory로 옮긴다.

## 새 서비스는 "추가"하는 게 아니라 첫 사용 때 캐시된다
절차를 사람이 직접 작성하지 않는다. `DERIVE REQUIRED`가 나오면 Agent가 현재 공식 문서를 확인하고 `bin/portwright memory draft procedure <service-id>`로 draft를 만든다. 검증된 Procedure만 정식 Memory로 옮긴다. 사람은 원클릭 OAuth처럼 사람만 할 수 있는 연결 단계만 맡는다. 컴파일할 설정이나 갱신할 Gateway는 없다.

## 썩음 점검
- `bin/portwright check`를 실행해 정식 Memory와 `_drafts/`를 함께 검사한다. 2.0.0부터 `_private` 루트도 함께 검사한다.
- `service_version`이 현재와 다른 `failures/` 교훈 → `status: stale` 후보(옛 버전에서 맞던 수정이 지금은 틀릴 수 있음).
- 2.0.0부터 `bin/portwright update`가 충돌 검사 뒤 자체 가드된 `git pull --ff-only`를 수행한다. 먼저 `git pull`을 실행하지 않는다. 그 뒤 stale 후보를 재검증한다: `status: stale`이거나 `last_verified`가 90일 이상 지난 추적 노트를 로컬 계약에 대고 다시 검사해 `revalidated`에 센다(로컬 노트 스키마 검증만). 별도로 `freshness_evidence.url`을 선언한 유효한 distributable 공개 Procedure는(공식 문서의 최종 HTTPS URL. 인증·쿼리·리디렉션이 필요한 URL은 제외) apply 때 그 문서를 내려받아 `services/_private/_evidence/<service>.json`에 캐시한다. 먼저 `bin/portwright update --dry-run --json`을 실행한다: 아무것도 내려받지 않고 `docs_planned`와 `stale_candidates`, `private_excluded`, `unclassified`, `refused_conflicts`를 보고한다. apply 실행은 `docs_fetched`, `docs_changed`, `docs_failed`, `docs_unconfigured`를 보고한다. `docs_failed`가 비어 있지 않으면 종료코드는 0이 아니고, 필드가 없는 Procedure는 갱신된 척하지 않고 `docs_unconfigured`에 올린다. 그 뒤 `bin/portwright update`를 실행한다.
- `preflight`는 명시적 증거 플래그가 없을 때 캐시된 증거 문서의 URL이 노트의 `freshness_evidence.url`과 같으면 그 문서를 자동으로 읽고 URL·digest·fetched_at을 보고한다. 내려받은 문서만으로는 신선도가 증명되지 않는다. `fresh`는 여전히 명시적으로 검증된 버전 일치(또는 `fetched_at <= last_verified`)가 필요하다.
- (감사로그 기반 완전 자동 신선도는 `FUTURE.md`에 남긴다. preflight가 보고하는 것은 `ARCHITECTURE.md` 7절의 2.0.0 신선도 결정표가 커버한다.)

## 노트 분류 (2.0.0)
- 추적되는 모든 노트는 `distributable: true|false`를 가진다. `true`는 `update`의 자체 fast-forward pull로 배포되고, `false`는 `services/_private/` 또는 `failures/_private/`(gitignore, 같은 카탈로그가 읽고 `check`도 검사)에 둔다.
- 본문이나 frontmatter에 특정 계정, 호스트, 개인 경로, 내부 프로젝트명, 볼트 항목이 있으면 `false`. 일반 공개 SaaS 절차는 `true`로 둔다.
- 필드가 없는 추적 노트는 `update`의 `unclassified` 목록에 뜬다. 무시하지 말고 분류한다.

## JEV 픽스처 테스트 (2.0.0)
- 판단 코드 경로는 테스트에서 실제 키가 필요 없다: `PORTWRIGHT_JEV_FIXTURES=tests/fixtures/jev`를 설정하면 합성 결정적 테스트 더블을 재생한다(녹음된 실제 API 응답이 아니다). `TYPESAFE_API_KEY`도 픽스처도 없으면 판단은 secret-safe 오류와 함께 `unknown`을 돌려준다. 버그가 아니라 의도된 폴백이다.

## 개인 스킬 원격 관리와 문서 감시

- `python3 scripts/classify_personal_skills.py screen`으로 로컬 패키지의 모든 파일을 검사한다. 검사 통과는 게시 승인이 아니다. 차단·개인 항목은 로컬에 남기며, 검사하지 못한 파일이나 외부 심링크를 조용히 제외한 채 승인하지 않는다.
- 승인한 복사본은 `skills/personal/<name>/`에 두고, `install/personal-skills-inventory.json`에 각 파일의 경로·크기·SHA-256을 묶는다. 참고 파일과 라이선스를 보존하며 설치된 원본은 변경하지 않는다.
- Actions 게시기는 기존 절차·포트라이트 가이드·승인된 개인 스킬을 하나로 합친다. 패키지별 목록과 전체 리소스 목록이 정확히 같아야 한다. 개인 리소스가 누락·이동·추가·변경되면 외부 모델 호출이나 게시 전에 다시 승인받는다.
- 승인 목록의 해시와 일치하는 개인 스킬은 JEV에 보내지 않는다. 공개 절차 검사는 별도로 유지한다. 이름만 일치하는 예외 목록으로는 게시를 허용하지 않는다.
- 인증된 gisul 레지스트리는 지시문과 참고 리소스를 제공하며 플러그인 코드를 원격 실행하지 않는다. 읽기 클라이언트는 파일 해시를 검사한다. 알 수 없는 확장자나 바이너리로 분류된 파일은 보관되더라도 지시문 전용 클라이언트의 텍스트 읽기는 거부될 수 있다.
- Cron은 현재 불변 릴리스의 해시로 검증한 `watch-sources.json`을 읽는다. 첫 수집은 JEV 없이 기준값만 저장하며 이후 변경분을 판단한다. 판단·이슈 발행 실패는 다음 실행에서 다시 시도하고, 처음 내용으로 돌아온 경우도 변경으로 처리한다.
- 설정된 실행 시각은 UTC `17 3 * * *`, 한국시간 매일 12:17이다. 주기는 항상 `wrangler deploy --triggers '<cron>'` 로 명시해 적용한다. 인자 없는 배포는 설정 값으로 되돌려 주지 않으며, 출력의 `schedule:` 줄은 적용 의도일 뿐이므로 `watch/state.json` 의 갱신 여부로 확인한다. 알림 이슈에는 URL·해시·판정값·릴리스 커밋만 쓰고 원문과 자격증명은 넣지 않는다.
- 이 개인 배포의 `GISUL_GITHUB_TOKEN`은 `<your-account>/<your-private-repo>`의 Issues 읽기·쓰기와 필수 Metadata 읽기만 허용한다. 콘텐츠 쓰기용 키가 아니므로 이 키로 gisul 쓰기 도구를 활성화하지 않는다.
- 맥미니에는 읽기 자격증명만 배치했다. 배포 코드와 런타임 준비가 Cloudflare 관리 권한을 뜻하지는 않으며, 배포 권한이 필요하면 해당 호스트에서 별도로 인증한다.

## 공개 배포 내보내기 (2026-09-22)
- 이 저장소는 작업용 비밀 저장소이며 영구히 비공개로 둔다. 지금 `_private/`에 있는 노트들은 한때 공개 경로로 커밋됐으므로 이 히스토리는 공개 저장소의 출발점이 될 수 없다. 공개 저장소는 빈 상태로 새로 만들고, 이 저장소를 포크·복제·히스토리 세탁해서 쓰지 않는다.
- 먼저 `python3 scripts/export_public.py --dry-run`으로 목록을 확인하고, 그 다음 `python3 scripts/export_public.py --out build/public`으로 만든다. 첫 게시 전에는 출력된 파일 목록을 반드시 검토한다.
- 대상 선정은 `install/public-export.json`이 기계적으로 한다: 포함 접두어·파일에서 제외 목록을 뺀 뒤, `services/`·`failures/`는 유효하고 `distributable: true`인 노트만 넣는다. `_private`, `_drafts`, `_evidence`, `profiles`는 공용 번들 가드가 거부하며, 심링크·바이너리·대용량·비밀 의심 문자열도 함께 거부한다. `skills/personal/`, `docs/research/`, `.github/`, 개인 스킬 인벤토리·게이트 예외 파일, 개인용 스크립트 2개는 이름으로 제외한다.
- 개인 표식은 `substitutions`로 일반화한 뒤 정규식 `personal_markers`로 검사한다. 하나라도 남으면 파일을 한 개도 쓰지 않고 중단한다. 그래서 공개 문서에 개인 표식을 적는 일은 검토 대상이 아니라 빌드 실패다.
- 검증은 산출물 안에서 자체 테스트를 돌려서 한다: `cd build/public && python3 -m unittest discover -s tests -q`, 그리고 `./bin/portwright check`. 커밋 전에 생성된 `__pycache__`는 지운다. 내보낸 `.gitignore`가 이미 제외하고 있다.


## 실패 모드
- **낡은 절차가 새어나감** → 사용자가 틀린 경로로 감. 대응: verify-before-instruct를 사후가 아니라 규칙으로.
- **Composio 다운** → 연결 도구에 행동 불가. 관리형 서비스이니 Composio 상태 확인, 로컬 수정 없음.
