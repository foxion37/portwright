# ARCHITECTURE - portwright (왜 이 모양인가)

> 개인용 v1을 왜 이렇게 짓는가. 바로 만들려면 `HANDOFF.md`. 미뤄둔 거버넌스/게이트웨이 비전은 `FUTURE.md`.
>
> 🌐 영문 사본: **`ARCHITECTURE.md`**. 여기 내용을 고치면 그쪽도 같이 고쳐야 합니다 (CLAUDE.md "이중 언어 파일 규칙").

---

## 0. 이게 뭔가
portwright는 처음에는 개인용으로 만든 **도구사용 지식 레이어**입니다. 이제 외부 개발자, 회사 동료, 비개발자에게 넓히되 두 목표를 유지합니다. 사용자의 설정 개입 **(A)**과 에이전트의 도구사용 실패·토큰 비용 **(B)**을 줄입니다. 도구마다 어떻게 **연결**하는지(별도 연결 백엔드가 자격증명을 보관)와 어떻게 **올바로 쓰는지**(Procedure와 Lesson 캐시)를 기억합니다. 개인용 설치는 이미 동작하지만, 공유 공개·회사 Hub는 M2·M3에서 만들 계획입니다. 게이트웨이는 없습니다. **2.0.0**부터 Profile이 프로젝트 디렉터리를 GitHub 계정, env 소스, DB와 묶어 올바른 컨텍스트의 캐시를 돌려줍니다(7절).

## 1. 목적 (진짜 목표)

**두 결과, 여섯 목적.** (A) *사용자*의 개입 최소화; (B) *에이전트 자신*의 도구사용 실패/토큰 최소화.

*(A) 사용자 개입 최소화*
1. 사용자가 하는 외부 도구 설정을 최소화한다.
2. 이미 연결한 도구를 또 연결하게 하지 않는다.
3. 사용자가 안 해도 되는 걸 시키지 않는다.
4. 결론: 최초 최소 토큰/API 키만 한 번 받아 기억하고, 나머지는 알아서 한다.

*(A+B) 항상 정답 절차로*
5. 각 도구의 절차를 최신·정답으로 유지해 **사람도 에이전트도** 낡거나 틀린 경로로 가지 않게 한다 — 사람 단계는 *그 자리에서 도출*(예: "Dashboard → API 키 입력"인데 지금은 "Dashboard → Settings → API/OAuth → Projects → Key"), 에이전트 자기 호출은 *캐시된 정답*으로.

*(B) 에이전트 도구사용 비용 최소화*
6. 에이전트 자신의 도구사용 비용을 최소화한다: 어렵게 얻은 연결 **및 사용** 절차와 실패를 캐시해, 다음엔 헛발질을 토큰으로 다시 치르지 않고 **첫 시도에 성공**한다.

목적 1-4는 **연결/자격증명** 문제 → 연결 백엔드가 푼다 — **Composio는 한 선택지, 네이티브 MCP/CLI/직접 API도 동급 대안.** 목적 5-6은 **절차 지식** 문제 → 이 레포의 `services/` + `failures/` + verify-before-instruct가 푼다. 여섯 중 *거버넌스/차단* 문제는 없다. 그래서 v1엔 게이트웨이가 없다.

## 2. v1 결정 (확정)

| # | 결정 | 근거 |
|---|------|------|
| **D1** | **v1엔 게이트웨이 없음.** | 여섯 목적은 사용자-개입-최소 + 에이전트-도구사용-비용-최소지 거버넌스가 아니다. 감시 게이트웨이(정책/감사/차단)는 다른 목표 → `FUTURE.md`. 비개발자 혼자가 필요도 없는 24시간 Docker 서비스를 지킬 이유 없다. |
| **D2** | **연결 백엔드** = 연결·OAuth, 자격증명 기억. **Composio는 한 선택지; 네이티브 MCP/CLI/API도 동급.** | 목적 1-4 대응(연결 1회, 토큰 재요청 없음). 실제로 작업을 *실행*할 수 있는 백엔드가 정답 — 네이티브 Vercel CLI/MCP가 실제 배포를 해낸 건 Composio 세션을 메타 전용으로 발급한 자기설정 실수(진단 후 수정함 — `services/composio.md`) 때문이었고 네이티브가 막힘 없이 더 가까웠기 때문. Composio도 세션에 앱 툴킷을 넣고 그 앱을 OAuth 연결하면 실행된다. Composio는 개인용 안전(SOC2/ISO, LLM에 안 들어가는 브로커드 토큰)이지만 *정체성은 아니다*. |
| **D3** | **verify-before-instruct + 도구사용 지식 캐시로 "항상 정답 절차", 여기에 결정적 신선도표 + JEV 보조** | 목적 5-6 해결. 사람 단계: 지시 직전 현재 문서에서 도출. 에이전트 자기 호출: 캐시된 절차/교훈을 먼저 읽어 재실패 방지. 2.0.0부터 `preflight`는 결정적 신선도표(7절)로 상태를 보고한다: 증거 버전이 `version_tag`와 같으면 `fresh`, 다르면 `stale`; `fetched_at`만 있으면 `<= last_verified`일 때 `fresh`, 아니면 `unknown` + verify-required; 증거가 없으면 `unknown` + verify-required. 자격증명이나 판단 응답이 없으면 Noul 보조 문장만 빠진다(규칙 1-2는 결정적이라 JEV 가 필요 없고, JEV 질문을 해야 했는데 자격증명·픽스처가 없으면 근거 문장에 그 사실을 적는다). `unknown`은 절대 `fresh`가 아니다. TypeSafe JEV(Choice/Score/Noul, 텍스트 생성 없는 결정 전용)가 값싼 판단을 맡는다: 라우팅 선택, Noul 신선도 보조, 메뉴 선택, tier 점수. Noul은 stale 근거를 보강할 뿐 결정적 결과를 뒤집지 못한다. 이게 IP, "도구 연결 **및 사용** 절차용 Context7". |
| **D4** | 데이터 = `services/` + `failures/` + `profiles/` (+ `_private` 루트) | `services/` = 현재 절차; `failures/` = 모든 도구사용 실패의 교훈, 낡은/틀린 사람-단계 경로 **또는 에이전트 자기 런타임 실수**(예: 잘못된 SDK/파라미터). 2.0.0부터: `profiles/` = 프로젝트별 컨텍스트 묶음, `services/_private/` + `failures/_private/`(gitignore) = `distributable: false` 노트 자리. 개인 노트는 워크스테이션을 떠나지 않지만 같은 카탈로그가 읽는다. `policy/`·`allowlist/`는 강제하려면 게이트웨이 필요 → `FUTURE.md`. |
| **D5** | **콘텐츠와 그것을 나르는 최소 인프라**를 관리합니다. `portwright update`는 보호된 코드 업데이트 경로로 남고 tier는 **조언**입니다. | 개인 캐시와 검사를 거친 단방향 공개 코드 내보내기는 따로 유지합니다. `scripts/export_public.py`는 `install/public-export.json`으로 내보낼 대상을 고르고 개인 표식이 남으면 중단합니다. 이 작업 저장소는 과거 개인 노트가 git 기록에 남아 있으므로 비공개로 유지합니다. 계획된 공유 배달은 Hub마다 **Worker 한 개, D1 한 개, Actions workflow 한 개**를 씁니다(M2 공개, M3 회사). D1은 접수·이벤트·토큰 해시·비용을, 비공개 commons의 git 사본은 공유 노트 본문·`grade`·회수 표식의 정본을 맡습니다. 코드 업데이트와 별도로 `_hub`에 콘텐츠를 동기화하며 로컬 우선순위는 `_private` → 추적 → `_hub`입니다. Hub의 접수 인증과 게시 검사는 외부 도구 호출을 중계하거나 통제하지 않으므로 D1의 게이트웨이 금지는 그대로입니다. 정책 집행과 감사 파이프라인도 만들지 않습니다. ADR-0004에 따라 중앙 절차가 없거나 낡으면 공식 문서에서 도출해 캐시하며, 미리 채우는 등록소로 바꾸지 않습니다. [승인된 변경 요청](CHANGE_REQUESTS_KR.md)을 참고하세요. 기존 2.0.0 `update`의 증거 갱신도 4절과 7절에 적힌 대로 유지합니다. |
| **D6** | 외부 개발자, 회사 동료, Client의 Agent를 이용하는 비개발자가 사용하며 공개 Operator와 회사 관리자의 역할은 다릅니다. | 공개 Operator는 초대 토큰을 발급하고 `stable` 승격을 검토하며 `stable` 회수를 확인합니다. 회사 관리자는 회사 토큰을 발급합니다. M3 회사 Hub는 회사 측 권한을 확인한 뒤에만 구축합니다. [승인된 변경 요청](CHANGE_REQUESTS_KR.md)을 참고하세요. |

## 3. 왜 게이트웨이를 뺐나 (우리가 제거한 것)
초기 초안은 위험 행동 차단·감사·멀티클라이언트 정책을 위해 감시 게이트웨이(Lunar MCPX)를 중심에 뒀다. 스파이크(`docs/adr/0003`)와 목적 재정의로 드러난 것: (a) 게이트웨이의 가치는 *거버넌스*인데 여섯 목적에 없다; (b) Lunar 정책은 allow/block뿐(사람 개입 "확인" 없음); (c) 쿼리 가능한 감사로그는 유료 티어로 보인다. 그래서 게이트웨이와 그게 강제하던 모든 것을 `FUTURE.md`로 옮겨 선택적 안전층으로 둔다. v1의 위험 행동 안전은 Claude Code 자체 권한 프롬프트가 커버한다.

## 4. 도구사용 지식 메커니즘 (목적 5-6, IP)
- `services/`는 **Procedure 캐시**지 미리 채운 등록소가 아니다. `services/<id>.md`는 그 Service를 한 번 써서 검증된 *뒤에야* 생긴다.
- **두 모드, 한 메커니즘:** (i) *사람 단계* — 넘기기 직전 도구의 현재 문서에서 경로를 **새로 도출**(항상 최신)하고 필요시 캐시; (ii) *에이전트 자기 호출* — 캐시된 절차/교훈을 **먼저** 읽어, 자신(또는 과거 실행)이 이미 겪은 실패를 다시 치르지 않음. 어느 쪽이든 틀린/어렵게 얻은 경로는 `failures/`의 **Lesson**이 된다. 즉 에이전트 몫은 "신선도 면제"가 아니라 *캐시 제공* 대상(목적 6).
- 일반 규칙이다, 손으로 적은 목록이 아니라 *모든* 도구 커버. 2.0.0부터 `portwright update`가 자체 가드된 fast-forward pull 뒤 stale 후보(`status: stale`이거나 `last_verified`가 90일 이상 지난 노트)를 로컬 계약에 대고 다시 검사해 보고하고, `freshness_evidence.url`을 선언한 Procedure의 캐시된 증거 문서를 갱신한다. 내려받은 문서는 증거 입력이지 신선도 증명이 아니므로, 배포가 신뢰도도 함께 새로 고친다. (감사로그 기반 완전 자동추적은 `FUTURE.md`에 남긴다.)
- 연결 백엔드(실행 가능하면 Composio, 아니면 네이티브 MCP/CLI)가 대부분의 수동 단계를 아예 없앤다(연결 1회) → 낡은 *사람* 지시 표면은 진짜 최초 설정으로만 줄고, *에이전트 사용* 캐시는 매 호출마다 이득을 낸다.

## 5. 내용물 구조
| 폴더 | 역할 | 형식 | 읽는 주체 |
|------|------|------|-----------|
| `services/` | Procedure **캐시** (도출-온-미스, 미리 안 채움) | `.md` (frontmatter + 본문) | 에이전트 |
| `failures/` | 교훈 캐시 (모든 도구사용 실패, 낡은/틀린 사람-단계 경로 또는 에이전트 자기 런타임 실수) | `.md` (frontmatter + 본문) | 에이전트 + 나 |
| `profiles/` | 프로파일 라우터 표, 프로젝트 컨텍스트당 파일 하나(경로, GitHub 계정, env 소스, DB, 기본 tier, 호스트) | `.md` (frontmatter + 본문) | `preflight` |
| `services/_private/`, `failures/_private/` | 개인 루트, `distributable: false` 노트, gitignore, 같은 카탈로그가 읽음 | `.md` (frontmatter + 본문) | 에이전트 + 나 |
| `services/_hub/`, `failures/_hub/` (M2 계획) | git이 추적하지 않는 Hub 콘텐츠 캐시. `_private`와 추적 노트보다 나중에 조회함 | `.md` | 로컬 Agent |

스키마: service frontmatter `id, display_name, version_tag, last_verified, endpoint{type,server}, human_steps[], agent_can[], status` + 선택 `profile_ids[]`, `distributable`; failure frontmatter `date, service, service_version, status` + 선택 `profile_id`, `distributable`; profile frontmatter `id, project_path, github_account, env_source{kind,project,environment,injector}, databases[], services[], default_tier, host`. (`endpoint`은 MCP 전송, `CONTEXT.md` 참고.)

## 6. 미룬 것
기존 멀티유저 보류 범위 가운데 일부는 승인됐습니다. M2에는 초대제 공개 Hub를, M3에는 별도로 격리한 회사 Hub를 계획합니다. 두 Hub는 외부 도구 호출을 중계하지 않고 콘텐츠를 배달합니다. 거버넌스·안전 게이트웨이 전체, 강제 정책과 역할 허용목록, 감사 기반 실패 수집, PII 마스킹, 스키마 표준 공개는 계속 미뤄 둡니다. 접수 인증과 게시 검사는 게이트웨이가 아닙니다. `FUTURE.md`와 [승인된 변경 요청](CHANGE_REQUESTS_KR.md)을 참고하세요. §1의 여섯 목적과 D1은 바꾸지 않았습니다.

## 7. 프로파일 라우터 (2.0.0)

**해결하는 문제:** 한 운영자가 한 워크스테이션에서 GitHub 계정, env 소스, DB가 다른 여러 프로젝트를 돌린다. v1 캐시 히트는 *틀린 계정의 맞는 절차*를 돌려줄 수 있었다. 노트는 맞는데 컨텍스트가 틀린 것이다. 프로파일 라우터는 이 경계를 고친다: `preflight`가 *어떻게* 답하기 전에 *어느 프로젝트*를 섬기는지 먼저 정한다.

**해결 순서:** (1) `--profile <id>`가 주어지면 그것. (2) cwd에 대한 `project_path` 최장 접두 매칭. (3) 후보가 0개거나 2개 이상 남으면 JEV **Choice** 판단이 고른다. (4) 못 정하면 `profile: null` + `rationale`, 절차는 그대로 돌려준다.

**preflight가 돌려주는 것 (2.0.0):** 해결된 `profile`(또는 null), Procedure, active Lessons, D3의 결정적 표에서 나온 `freshness` `{state: fresh|stale|unknown, compared, action: none|derive-required|verify-required}`, 조언용 `tier`, `rationale`. tier 우선순위: 명시 forbid 규칙 > 매칭된 규칙(`install/tiers.json`) 또는 Profile `default_tier` > JEV Score. 미매칭·판단 불가는 `confirm`. JEV는 명시 forbid를 낮추지 못한다.

**브라우저 선택:** `portwright browser select --candidates <file.json> --goal "<text>"`가 BrowserCandidate(`{ref, role, name, url?, snippet?}`) 하나를 JEV Choice로 고른다. 후보 없음, 신뢰도 0.6 미만, top-2 격차 0.15 미만이면 거부(`accepted=false`, `reject_reason`). 긁기는 Aside-Browser/Playwright의 일이고 portwright는 고르기만 한다.

**JEV:** TypeSafe Jev, Choice/Score/Noul만, 텍스트 생성 없음. 키(`TYPESAFE_API_KEY`)는 `opsvc` 경유 1Password에서 런타임에만 주입하고 저장하지 않는다. 테스트는 `PORTWRIGHT_JEV_FIXTURES`의 합성 결정적 픽스처 더블을 재생한다(녹음된 실제 API 응답이 아니다).

**증거 문서 캐시:** 유효한 distributable Procedure는 `freshness_evidence.url`을 선언할 수 있다(공개 공식 문서의 최종 HTTPS URL. 인증·쿼리·리디렉션 URL 제외). `portwright update`는 apply 때만 그 문서를 내려받아 `services/_private/_evidence/<service>.json`에 캐시한다. dry-run은 `docs_planned`만 보고하고 다운로드하지 않는다. `preflight`는 명시적 증거 플래그가 없고 캐시된 문서의 URL이 일치하면 그 문서를 자동으로 읽어 URL·digest·fetched_at을 보고한다. 문서는 조언용 Noul 검사의 입력이고, 다운로드만으로 `fresh`가 증명되지는 않는다.
