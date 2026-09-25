# HANDOFF - portwright 빌드 순서 (v1 + 2.0.0)

> 코드 에이전트에 붙여넣어 시작. 배경: `ARCHITECTURE.md`. 미뤄둔 게이트웨이 비전: `FUTURE.md`. 스키마: 각 폴더의 `_TEMPLATE`.
>
> 🌐 영문 사본: **`HANDOFF.md`**. 여기 내용을 고치면 그쪽도 같이 고쳐야 합니다 (CLAUDE.md "이중 언어 파일 규칙").

---

현재 v2.1.0에서는 누구나 임의의 경로에 로컬 CLI를 복제해 사용할 수 있습니다. 공유 공개 Hub는 M2, 별도 회사 Hub는 M3 계획이며 둘 다 아직 제공되지 않습니다. 아래 v1 및 2.0.0 빌드 순서는 당시의 결정 기록이지 현재 로컬 CLI를 1인용으로 제한하는 규칙이 아닙니다. 자세한 역할은 [역할과 범위](docs/roles-and-scope_KR.md)를 참고하세요.

## 0. 미션 (v1)
(1) 외부 도구를 연결 백엔드(Composio든 네이티브 MCP/CLI든)로 한 번 연결하고 기억하며, (2) 에이전트가 사용자에게 주는 수동 단계가 낡은 경로가 아니라 *현재* 절차에서 나오게 하고, (3) 에이전트가 부딪힌 절차/실패를 캐시해 다음엔 헛발질을 다시 치르지 않고 첫 시도에 성공하게 하는 개인용 레이어. 게이트웨이 없음. 운영은 나 + Claude Code, 혼자.

## 1. 확정 결정 (재논의 금지)
| 항목 | v1 결정 |
|------|---------|
| 범위 | 사용자 개입 최소 + 에이전트 도구사용 비용 최소(연결 + 사용 지식). 거버넌스/게이트웨이 → `FUTURE.md` |
| 연결 | 연결 백엔드(**Composio**든 네이티브 MCP/CLI든), 1회 연결 + 자격증명 기억 |
| 신선도 | **verify-before-instruct** — 수동 단계 지시 전 절차를 읽고 최신인지 확인 |
| 데이터 | `services/`(현재 절차) + `failures/`(교훈). `policy/`+`allowlist/` → FUTURE |
| 인프라 | **없음.** 게이트웨이도, 정책 엔진도, 감사 파이프라인도 없음 |
| 운영자 | 나 + Claude Code, 혼자 |

## 2. 절대 규칙
1. **필요 없는 인프라를 짓지 마라.** v1엔 게이트웨이 없음.
2. **토큰/시크릿을 코드·문서·레포·예시에 절대 금지.** 자격증명은 Composio 보유; 런타임만. (`.gitignore`가 `.env`·`*.key`·`secrets/` 차단.)
3. **모든 절차에 `last_verified`.** 최근 확인 없는 절차는 의심 대상.
4. **`human_steps` = 불가피한 사람 단계만**, 그리고 *현재* 도구 UI를 반영해야 함. 불가피한 최소를 넘는 "토큰 받아와 / 가입해 / 직접 설정해"가 바로 이 프로젝트가 없애려는 안티패턴.
5. **지시 전에 확인하라.** 절차가 최신인지 확인 없이 사용자에게 설정 단계를 주지 마라.

## 3. 빌드 순서
1. **백엔드 연결** — 가장 많이 쓰는 도구 1개(실행되면 Composio, 아니면 네이티브 MCP/CLI)(예: Vercel). 원클릭 OAuth, 1회. 에이전트가 토큰 다시 안 물어보고 그걸로 행동하는지 확인. 이것으로 목적 1-4 달성.
2. **절차 확보 규칙을 만든다(일반, 모든 도구).** Claude Code 스킬 또는 `CLAUDE.md` 규칙으로 노출. 규칙: 수동 단계를 시키기 전 `services/<id>.md`를 본다; 신선하면 사용; **없거나 낡았으면 그 도구 공식 문서에서 현재 절차를 도출해 `services/<id>.md`에 캐시**(`last_verified` 올림); 낡은/틀린 경로를 잡았으면 `failures/` 교훈 기록. 이게 IP고 *도구 무관*이어야 한다 — 도구별 손작성 목록은 절대 아님.
3. **캐시가 알아서 차게 한다 — Vercel은 그냥 첫 항목.** 라이브러리를 손으로 쓰지 마라. Vercel로 루프를 돌린다: 에이전트가 Vercel 현재 절차를 도출하고, 1회성 부분만 형을 안내하고, `services/vercel.md`를 첫 캐시·검증 항목으로 쓴다. (예시로 1회 시드해도 되지만, 핵심은 수동 작성이 아니라 메커니즘이다.)

## 4. v1 완료 = 되는 걸 증명 (토큰 메트릭이 아니라 시연으로)
> 에이전트가 가장 많이 쓰는 도구에서 실제 작업(예: Vercel 배포)을 **어떤** 연결 백엔드로든 **사용자 개입 최소**(토큰 재요청 없음, 수동 대시보드 단계 없음)로 해낸다. 그리고 부딪힌 절차/실패가 **캐시**돼 2번째 실행은 같은 헛발질을 건너뛴다: 도구 설정 경로가 바뀌면 에이전트가 옛 경로로 보내는 대신 **잡거나 갱신**하고; 에이전트 자기 호출이 한 번 실패했으면 캐시된 교훈이 2번째 실행에서 그걸 피하게 한다. (B) 결과는 **시연으로** 증명한다, 캐시된 실패를 눈에 띄게 피하는 2번째 실행, 토큰을 세서가 아니다.
>
> **2.0.0은 자기 시연을 추가한다:** 프로파일 전환 시연(GitHub 계정과 DB가 다른 두 Profile이 각각 올바른 컨텍스트를 돌려줌), 신선도 시연(일부러 낡힌 Procedure가 `stale` 판정, 증거 없음 케이스는 `unknown` + verify-required, 절대 `fresh` 아님), JEV 대 LLM 비용표(`docs/research/2026-09-21-jev-cost.md`), 기존 게이트 그린(unittest 41건 이상, `check` 실패 0, `docs/research/2026-09-21-v2-validation.json`에 기록).

## 5. 하지 말 것 (안티패턴)
- ❌ v1에 게이트웨이/정책 엔진/감사 파이프라인 세우기 (전부 `FUTURE.md`).
- ❌ 서비스 라이브러리를 손으로 작성하기(대신 도출-온-미스).
- ❌ 절차가 최신인지 확인 없이 수동 단계 지시.
- ❌ `last_verified` 누락. ❌ 어디에도 토큰 하드코딩.

## 6. 참고 문서
- `ARCHITECTURE.md` — v1 결정과 왜 게이트웨이가 없는지.
- `README.md` — 폴더 역할 + v1 구조.
- `MAINTENANCE.md` — 혼자 절차 최신 유지.
- `FUTURE.md` — 미뤄둔 게이트웨이/거버넌스 (+ Lunar 스파이크 결과, ADR-0003).
- `services/_TEMPLATE.md`, `failures/_TEMPLATE.md` — 스키마.

## 7. 2.0.0 빌드 순서
2026-09-21 승인(`CHANGE_REQUESTS.md`). 시드: `~/.ouroboros/seeds/portwright-v2-profile-router-20260921.yaml`. 조사: `docs/research/2026-09-21-vnext-survey.md`. D1은 그대로다: 게이트웨이 없음, tier는 조언.

1. **Profile + 프로파일 인식 preflight.** `profiles/<id>.md`(경로, GitHub 계정, env 소스, DB, 서비스, 기본 tier, 호스트). `preflight`는 cwd에 대한 `project_path` 최장 접두 매칭으로 Profile을 정하고, 애매하면 JEV Choice, 못 정하면 `null` + 근거.
2. **신선도.** 결정적 판정표(버전 일치 → fresh/stale. 일치는 버전이 같다는 증거이지 절차 전체가 여전히 정확하다는 증명이 아님; `fetched_at`만 있으면 `<= last_verified`면 fresh, 아니면 unknown + verify-required; 증거 부재 → unknown + verify-required; JEV 자격증명·판단 부재는 조언용 Noul 보조 문장만 빠질 뿐 결정적 규칙이 이미 답했을 때 `unknown`을 강제하지 않음; JEV Noul은 stale 근거 보강만). `unknown`은 절대 `fresh`가 아니다.
3. **조언용 tier.** 명시 forbid > 매칭된 규칙(`install/tiers.json`) 또는 Profile `default_tier` > JEV Score. 미매칭·판단 불가는 `confirm`. 강제는 Client가 하고 portwright는 조언만 한다.
4. **브라우저 선택.** `portwright browser select`가 JEV Choice로 후보 하나를 고른다. 후보 없음, 신뢰도 0.6 미만, top-2 격차 0.15 미만이면 거부.
5. **update + 개인 분리.** `portwright update`(충돌 검사 뒤 자체 가드된 `git pull --ff-only`를 직접 수행하므로 먼저 pull 하지 않음. stale 로컬 계약 재검증, `freshness_evidence.url`을 선언한 Procedure의 증거 문서 갱신(apply 때만), 개인 노트 제외, 미분류, 거부된 충돌 보고). `distributable` 필드. `services/_private/` + `failures/_private/` gitignore 루트.
6. **비용표.** 실측 세션 1회(LLM 대 JEV 같은 판단 N>=20쌍) → `docs/research/2026-09-21-jev-cost.md`.
7. **검증 기록.** 게이트 + update 시나리오 + 개인 노트 매니페스트 → `docs/research/2026-09-21-v2-validation.json`.
