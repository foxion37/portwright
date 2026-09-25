# portwright

도구사용 지식 레이어의 도메인 용어집. 사용자 셋업 단계와 에이전트 도구사용 실패를 줄이는 여섯 목적은 그대로다. 개인용 로컬 기능에 더해 공유 Hub는 M2·M3에서 계획한다. 정식 용어는 영어(코드·문서 기준), 정의는 한국어. 구현 세부사항이 아니라 *말의 뜻*만 담는다.

> 이 파일은 **단일 파일**로 유지한다(EN/KR 분리 안 함). glossary는 자주 바뀌어 두 벌로 쪼개면 어긋나기 쉽다.

## Language

**User (사용자)**:
portwright를 **Client**의 **Agent**를 통해 사용하는 *사람*. 외부 개발자, 회사 동료, 비개발자를 모두 포함한다. 사람만 할 수 있는 단계는 직접 수행한다. **Operator**와 **회사 관리자**도 서비스를 사용할 때는 User다.
_쓰지 말 것_: operator(운영 권한과 사용자라는 지위를 혼동), account

**Client (클라이언트)**:
**Client**는 Agent를 실행하는 *앱*. Claude Code · Cursor · ChatGPT 같은 MCP 클라이언트를 포함한다.
_쓰지 말 것_: agent(앱을 에이전트라 부르지 말 것)

**Agent (에이전트)**:
**Client** 안에서 돌며 *도구 호출을 결정하는 AI*(예: Claude 모델). `agent_can`은 Agent의 능력 목록이다. 외부 도구 호출의 허용 여부는 Client 자체 권한이 결정하며 portwright는 조언만 한다.
_쓰지 말 것_: client, bot

**Operator (운영자)** *(공개 Hub: M2 계획)*:
공개 **Hub**의 초대 토큰을 발급하고 **Grade**의 `stable` 승격을 검토하며 `stable` **Recall**을 확인하는 사람. 회사 초대 토큰은 회사 관리자가 발급한다. 운영자는 외부 도구 호출을 통제하는 게이트웨이가 아니다.
_쓰지 말 것_: User(모든 사용자가 운영자인 것은 아님), Agent

**Hub (허브)** *(공개 M2·회사 M3 계획)*:
personal, public, company 중 한 대상에게 지식을 배달하는 격리된 서비스. 공유 Hub마다 Worker 한 개, 접수용 D1 한 개, 배달 버킷 한 개와 **Commons**의 Actions workflow 한 개를 쓴다. personal은 소유자의 기존 개인 배달을 유지한다. 공개·회사 Hub는 자격증명과 콘텐츠를 공유하거나 서로 대체 조회하지 않는다.
_쓰지 말 것_: Gateway(외부 도구 호출을 중계·강제하지 않음), 여러 대상이 섞인 단일 저장소

**Commons (공유 캐시의 git 정본 사본)** *(M2·M3 계획)*:
한 공유 **Hub**의 Procedure/Lesson 본문, **Grade**, **Recall** 표식을 담는 비공개 git 저장소. 한 대상의 공유 캐시를 보관할 뿐 미리 채운 등록소가 아니다. 중앙에 없거나 낡은 절차도 공식 문서에서 도출해 캐시한다(ADR-0004 유지). 회사 Hub는 별도 Commons를 쓴다.
_쓰지 말 것_: registry, DB가 노트 본문의 또 다른 정본

**Grade (노트 등급)** *(M2 계획)*:
공유 노트의 `trial|stable` 상태. `trial`은 게시 검사를 통과해도 명시적으로 요청할 때만 제공하고, `stable`만 기본 조회·preflight·번들에 들어간다. 서로 다른 초대 토큰 계보 세 건이 revision을 확인하고 **Operator**가 검토해야 stable이 된다.
_쓰지 말 것_: Tier(도구 호출의 조언용 자율성 등급), trial을 기본 주입

**Invite token (초대 토큰)** *(M2·M3 계획)*:
공개 **Hub**에서는 **Operator**가, 회사 **Hub**에서는 회사 관리자가 발급하는 접속 자격. 서버에는 해시, 조직, 권한(`read|submit|operator`), 만료, 비식별 발급 계보만 저장한다. 재발급은 독립적인 확인표를 늘리지 않는다. 이름·이메일·IP는 저장하지 않는다.
_쓰지 말 것_: 외부 서비스의 API 키, 사람 수와 동일한 토큰 수

**Recall (회수)** *(M2 계획)*:
**Commons**의 `note_id + revision digest` 표식으로 특정 revision의 배달을 중단하는 일. 현재·옛 릴리스 pin 조회에서 막고 로컬 캐시는 동기화 시 반영한다. 서로 다른 두 계보의 신고로 `trial`을 회수하고, `stable` 회수에는 **Operator**의 확인이 필요하다. 모델 예산과 무관하게 동작한다.
_쓰지 말 것_: git revert만으로 옛 릴리스나 오프라인 캐시까지 즉시 회수했다는 뜻

**Needless delegation (불필요한 위임)**:
**Agent**가 *스스로 할 수 있는 일*을 **User**에게 시키는 것. **portwright가 막으려는 핵심 안티패턴.** ("사람 손 많이 = 안 좋은 제품"의 정체.)
_쓰지 말 것_: 위임(불필요하지 않은 정상 요청과 구분)

**Agent's job (에이전트 몫)**:
**Agent**가 (직접·Composio·도출로) *할 수 있는* 행동. 절대 **불필요한 위임**이 되면 안 된다. frontmatter 필드 `agent_can`이 이걸 적는다.
_쓰지 말 것_: agent_can을 "사람이 해도 되는 것"으로 해석

**Human's job (사람 몫)**:
*사람만* 할 수 있는 불가피한 행동(예: OAuth 동의 클릭, 약관 수락). 최소로 줄인다. frontmatter 필드 `human_steps`가 이걸 적는다. 시킬 땐 **Verify-before-instruct**로 *현재* 경로.
_쓰지 말 것_: 안 해도 되는데 시키는 것(그건 **불필요한 위임**)

**Service (서비스)**:
연결 대상이 되는 *외부 시스템*(예: Vercel, Notion). `id`로 참조한다. `allowlist`·frontmatter가 가리키는 건 이 Service.
_쓰지 말 것_: service(= `services/<id>.md` 파일을 뜻할 때)

**Procedure (절차)**:
한 **Service**에서 무엇이 **에이전트 몫**이고 무엇이 **사람 몫**인지의 *현재 경계 지도*(= `agent_can` / `human_steps` + 하지 말 것). 설정이냐 사용이냐로 나누지 않고 *누가 할 수 있나*로 나눈다. **항상 현재 공식 문서에서 도출 가능**하며, 검증되면 `services/<id>.md`에 캐시된다.
_쓰지 말 것_: service(= 외부 시스템과 혼동), 매뉴얼, "사용 절차"(사용은 에이전트 몫이라 절차가 아님)

**Procedure cache (절차 캐시)**:
`services/` 폴더. 검증된 **Procedure**를 담는 *캐시*지 미리 채워두는 등록소가 아니다. 없으면 도출해 채우고, 자주 쓰는 것만 쌓인다. 그래서 *모든* **Service**를 커버한다(미리 적어둔 것만이 아니라).
_쓰지 말 것_: registry, 등록소, 라이브러리

**Connection (연결)**:
**User**를 대신해 한 **Service**에 행동할 수 있게 된 *인가된 링크*. Composio가 OAuth로 1회 수립·보유. 핵심 원칙 "최소 연결(least connection)" = User에게 맺은 Connection 수를 최소로.
_쓰지 말 것_: connection(= frontmatter 전송 필드와 혼동 → 그건 **Endpoint**)

**Endpoint (엔드포인트)**:
한 **Service**의 MCP 전송 주소(`type`, `server`). frontmatter 필드명도 `endpoint:`이며, 공유 **Hub**가 외부 도구 호출을 중계한다는 뜻은 아니다.
_쓰지 말 것_: connection

**Verify-before-instruct (지시 전 확인)**:
v1의 핵심 메커니즘(IP). **Agent**가 **사람 몫**을 **User**에게 시키기 *직전에* 현재 공식 문서에서 경로를 도출한다. **에이전트 자기 호출**은 캐시된 절차와 교훈을 먼저 읽어 재실패를 막는다.
_쓰지 말 것_: validation(코드 검증과 혼동)

**Verified (검증됨)** *(v1: 정보용 스탬프 / 자동추적은 FUTURE)*:
`last_verified` = 그 **Service**의 사람 몫 경로를 *현재 문서에 대고 마지막으로 확인한 날*(에이전트가 지시할 때 스탬프). v1엔 게이트웨이·감사로그가 없어 자동 갱신 불가 → 감사로그 기반 자동 신선도는 FUTURE. v1 신선도는 추적이 아니라 **지시 직전 도출**로 보장.
_쓰지 말 것_: "절차를 다 확인했다"는 뜻

**Stale (썩음)**:
오래돼 못 믿을 상태. **Lesson**: `service_version`이 현재와 달라 더는 안 맞을 수 있음(`status: stale`). (절차의 "60일 자동 stale" 판정은 감사로그가 필요 → FUTURE; v1은 지시 직전 재도출로 대체.)
_쓰지 말 것_: 만료, expired

— 아래 셋은 **FUTURE 범위**(게이트웨이 레이어, `FUTURE.md` Layer A). v1엔 강제 주체가 없다. —

**Gateway (게이트웨이)** *(FUTURE)*:
모든 **Client**의 호출이 거치는 중앙 관문(후보 = Lunar MCPX). 정책 강제·감사 담당. **v1엔 없음.**
_쓰지 말 것_: engine (Gateway로 통일)

**Policy (정책)** *(FUTURE)*:
**Agent** 호출을 어떻게 다룰지 정한 규칙(`policy/tiers.yaml`). 강제하려면 **Gateway** 필요 → FUTURE.

**Tier (단계)** *(2.0.0: 조언 필드 / 게이트웨이 강제는 FUTURE)*:
호출의 자율성 수준 **auto/confirm/forbid**. 2.0.0부터 `preflight` 출력의 *조언* 필드다. 명시 forbid 규칙 > 매칭된 규칙(`install/tiers.json`) 또는 Profile `default_tier` > JEV Score 순으로 정하고, 미매칭·판단 불가는 `confirm`. JEV는 명시 forbid를 낮추지 못한다. 강제는 Client 자체 권한 프롬프트가 한다(게이트웨이 없음, D1 유지). 게이트웨이가 강제하는 AUTO/CONFIRM/FORBIDDEN 모델은 여전히 FUTURE.
_쓰지 말 것_: 차단 규칙(조언이지 강제가 아님)

**Failure (실패)**:
한 번의 에러 *사건* — 특정 호출이 깨진 일. (v1엔 감사로그 없음; 에이전트가 `failures/`에 기록.)
_쓰지 말 것_: bug, error(코드 에러와 혼동), 실패 기억

**Lesson (교훈)**:
하나 이상의 **Failure**를 분석해 만든 *재사용 가능한 교정 절차*. `failures/` 폴더의 `.md` 한 개가 곧 한 Lesson.
_쓰지 말 것_: failure(사건과 혼동), 실패 기록, postmortem

**First-try success (첫시도 성공)**:
에이전트가 과거 실패/절차가 캐시돼 있어 첫 호출에 성공하는 것 — 목적 (B)가 지키려는 가치.
_쓰지 말 것_: 토큰 측정/메트릭(=FUTURE)

**Profile (프로필)** *(2.0.0)*:
한 프로젝트의 컨텍스트 묶음: `project_path`, `github_account`, `env_source`(Infisical/1Password *참조 이름*만, 값 없음), `databases`, `services`, `default_tier`, `host`. `profiles/<id>.md`에 산다. preflight는 cwd에 대해 `project_path` 최장 접두 매칭으로 Profile을 고르고, 후보가 0개거나 2개 이상이면 JEV Choice가 고른다. 못 정하면 `profile: null` + 근거.
_쓰지 말 것_: account(계정 하나만이 아니라 번들), config

**Judgment (판단)** *(2.0.0)*:
JEV가 내리는 한 번의 결정. 종류는 route(Profile 선택), freshness(Noul 보조), menu(BrowserCandidate 선택), tier(Score 분류). `status`는 `ok|unknown`이고, `unknown`은 안전한 쪽(confirm, verify-required)으로만 떨어진다.
_쓰지 말 것_: 생성, 요약(JEV는 텍스트를 만들지 않는다)

**Freshness (신선도)** *(2.0.0)*:
캐시된 Procedure의 증거 비교 상태. `fresh`는 증거 버전 일치 또는 기존 검증 시점과의 날짜 비교 결과이며, 사용법 전체 재검증을 뜻하지 않는다. `stale`은 증거 버전이 다를 때, `unknown`은 증거가 없거나 시각만으로 확정할 수 없을 때다. 사용자가 승인한 "먼저 버전을 비교" 원칙에 따라 버전 비교에는 JEV 자격증명이 필요하지 않으며, JEV를 사용할 수 없으면 본문 판단의 보조 근거만 빠진다.
_쓰지 말 것_: 만료, expired

공식 문서의 취득 근거는 `freshness_evidence.url`로 설정하며 `update`가 본문을 개인 evidence 캐시에 저장한다. `preflight`는 명시적인 증거 입력이 없을 때 URL과 해시를 확인한 캐시를 사용한다. `docs_fetched`는 다운로드 성공 수에 대응하는 목록이고 `revalidated`는 로컬 노트 형식 검사 수다. 어느 쪽도 절차 전체를 자동 승인하지 않는다.

**Distributable (배포 가능)** *(2.0.0)*:
노트 frontmatter의 배포 플래그. `true`면 git 추적 트리에 남아 `portwright update`의 보호된 fast-forward로 배포된다. 사용자가 먼저 `git pull`을 실행하지 않는다. `false`면 **Private root**로 옮긴다. 추적 노트는 앞으로 이 필드가 필요하며, 없는 추적 노트는 `update`가 `unclassified`로 보고한다.

**Private root (개인 루트)** *(2.0.0)*:
`services/_private/`와 `failures/_private/`. gitignore된 개인 전용 노트의 자리. 같은 카탈로그가 두 루트를 함께 읽고 `check`도 둘 다 검사한다. 계정명·호스트·개인 경로가 들어간 노트는 여기.

**JEV (제브)** *(2.0.0)*:
TypeSafe Jev (https://docs.typesafe.ai/introduction). 텍스트를 생성하지 않고 Choice/Score/Noul 판단만 내리는 결정 전용 모델. API 키는 `TYPESAFE_API_KEY`로 런타임에만 주입한다(`opsvc` 경유 1Password, 저장 금지). 현재 `PORTWRIGHT_JEV_FIXTURES` 테스트 데이터는 수작업으로 만든 합성 응답이며 실제 API 응답 녹음이 아니다.

**BrowserCandidate / MenuSelection (브라우저 후보 / 메뉴 선택)** *(2.0.0)*:
`portwright browser select`의 입력과 출력. BrowserCandidate는 Aside-Browser/Playwright가 긁은 후보 하나(`ref, role, name, url?, snippet?`). MenuSelection은 JEV Choice가 고른 결과(`accepted`, `reject_reason`). 후보 없음, 신뢰도 0.6 미만, top-2 격차 0.15 미만이면 거부한다. portwright는 고르기만 하고 긁지 않는다.

## Relationships

- 하나 이상의 **Failure**(감사로그 사건)가 하나의 **Lesson**(`failures/` 항목)을 낳는다.
- 한 **Service**는 하나의 **Procedure**(`services/<id>.md`)를 가진다. **Lesson**은 보통 한 **Service**의 **Procedure**에 한 줄로 반영된다.
- 한 **User**가 여러 **Client**를 쓴다. 각 **Client** 안에서 한 **Agent**가 돈다.
- 여러 **User**가 각자의 **Client**와 **Agent**로 personal 또는 초대받은 **Hub**의 Procedure/Lesson을 읽는다. **Operator**와 회사 관리자는 각 Hub의 초대 토큰을 발급한다.
- 공유 **Hub**의 **Commons**가 노트 본문·**Grade**·**Recall** 표식의 git 정본 사본이고, D1은 접수·revision별 확인/신고·토큰 해시/계보·비용을 맡는다. 공유 캐시에 없거나 낡은 Procedure도 ADR-0004의 도출·캐시 경로를 따른다.
- 로컬 조회는 `_private` → 추적 → `_hub` 순서다. **Grade** `trial`은 명시 조회만, `stable`은 기본 조회에 포함한다. 공개·회사 **Hub** 간 대체 조회는 없다.
- 한 **Service**의 **Procedure**는 **에이전트 몫**과 **사람 몫**으로 갈린다. **Agent**는 에이전트 몫을 *스스로* 하고, 사람 몫만 **User**에게 — 그것도 최신 경로로 — 요청한다.
- 에이전트 몫을 User에게 시키면 그게 **불필요한 위임**(막을 대상)이다.

## Example dialogue

> **Dev:** "`failures/`에 실패를 쌓는다는 거죠?"
> **Domain:** "아니. 거기 쌓이는 건 **Lesson**이야. **Failure**는 한 번 깨진 *사건*이고(v1엔 감사로그 없음 — 에이전트가 겪고 기록), 그걸 분석해 '다음엔 이렇게'로 만든 게 **Lesson**. 폴더 이름이 `failures/`라서 헷갈리는데, 내용물은 Lesson이야."

## Flagged ambiguities

- "failure"가 *사건*과 *항목* 둘 다로 쓰여 충돌 → 해결: 사건 = **Failure**, 항목 = **Lesson**. 폴더명 `failures/`는 표지판으로 유지하되 내용물은 Lesson.
- "service"가 *외부 시스템*과 *기록 파일* 둘 다 → 해결: 외부 = **Service**, 기록 = **Procedure**. 폴더명 `services/` 유지.
- "agent"가 *AI*와 *앱*(Cursor 등) 둘 다 → 해결: 앱 = **Client**, AI = **Agent**. (`FUTURE.md`의 "beginner agents" 문구 = **Client**로 수정)
- "connection"이 *인가된 링크*와 *frontmatter 전송 필드* 둘 다 → 해결: 링크 = **Connection**, 필드 = **Endpoint**(필드명 `endpoint:`로 개명).
- "operator"가 *운영 권한*과 *일반 사용자*로 섞임 → 해결: **Operator**는 공개 Hub의 토큰 발급·stable 검토·stable 회수 확인 역할이고, **User**는 portwright를 쓰는 모든 사람이다. 회사 토큰 발급은 회사 관리자가 맡는다.
- "verified"가 *절차 전체 확인*으로 오해됨 → 해결: **Connection 신선도**만 뜻함(자동). Procedure 정확성은 **Lesson**이 담당.
- `services/`가 원래 뼈대에서 "registry/등록소(미리 채움)"로 정의됐으나 "모든 도구 적용"과 충돌 → 해결: **Procedure cache(도출-온-미스)**. 폴더 유지, 미리 안 채움. "registry/등록소"는 버린다.
- "Procedure가 설정 절차냐 사용 절차냐"가 흐릿 → 해결: 그 축이 틀렸다. 기준은 *누가 할 수 있나* = **에이전트 몫** vs **사람 몫**. 사용(배포 등)은 에이전트 몫이라 절차가 아님. 핵심은 에이전트 몫을 사람에게 떠넘기는 **불필요한 위임**을 막는 것.
- 신선도가 모든 절차에 필요한가 → 해결: **사람 단계 = 지시 직전 현재 문서에서 도출; 에이전트 자기 호출 = 캐시된 절차/교훈 먼저 읽어 재실패 방지(캐시 제공 대상).** 에이전트 몫도 "신선도 면제"가 아니라 캐시로 서빙된다(목적 6). 그리고 v1엔 감사로그가 없어 `last_verified` 자동추적 불가 → v1 신선도 = "사람 단계 지시 직전 현재 문서에서 도출". `Verified`/`Stale` 자동판정은 FUTURE.
