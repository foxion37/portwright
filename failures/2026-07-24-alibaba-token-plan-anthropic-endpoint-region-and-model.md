---
date: "2026-07-24"
service: alibaba-token-plan
service_version: "Token Plan Personal Edition; qwen3.8-max-preview / qwen3.7-plus / qwen3.6-flash"
status: active
distributable: true
---

# Token Plan Anthropic 엔드포인트 — 공식 문서의 지역이 우리 키와 다르고, thinking 은 못 끈다

## Symptom

1. 공식 Quick Start 문서가 안내하는 Anthropic 호환 URL 로 호출하면 401 이 난다:

```text
POST https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic/v1/messages
→ 401 {"code":"InvalidApiKey","message":"Invalid API-key provided."}
```

2. 응답이 너무 느려 클라이언트가 타임아웃한다(180초 초과).
3. `"thinking":{"type":"disabled"}` 를 넣으면 400 Bad Request.

## Correct root cause

1. **지역 불일치.** 공식 영문 문서는 `cn-beijing` 을 적어 두었고 "Token Plan 은 China(Beijing)
   에서만 제공"이라고 쓰여 있지만, 이 계정의 키(`sk-sp-`)는 **`ap-southeast-1`(싱가포르)** 소속이다.
   키가 잘못된 게 아니라 **지역이 다른 것**이라 오류 문구(`InvalidApiKey`)가 오해를 부른다.
   `portwright/services/alibaba-token-plan.md` 에 적혀 있던 `ap-southeast-1` 이 맞다.

   실측(2026-07-24):

   | Base URL | 결과 |
   |---|---|
   | `token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic` | 401 InvalidApiKey |
   | `token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic` | **200 OK** |

2. **`qwen3.8-max-preview` 는 추론형(thinking)이고 끌 수 없다.** Anthropic 호환 경로에서
   `thinking: disabled` 는 400 이다. 짧은 입력에도 thinking 토큰이 수천 개 나와 느리다 —
   한국어 180자 교정에 **235초 / 출력 10,046 토큰**. 타임아웃은 서버 문제가 아니라 이 특성이다.

## Fix

- Base URL 은 **`https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic`**,
  호출 경로는 `/v1/messages`, 헤더는 `x-api-key` + `anthropic-version: 2023-06-01`.
  (Claude Code 에 물릴 때 `ANTHROPIC_BASE_URL` 에는 `/apps/anthropic` 까지만 넣는다.)
- 타임아웃은 **600초 이상**으로 잡는다. thinking 을 못 끄므로 짧은 입력도 오래 걸린다.
- 모델은 용도로 고른다. 한국어 띄어쓰기 교정 실측(같은 입력, 2026-07-24):

  | 모델 | 소요 | 출력토큰 | 공백만 변경 검사 | 비고 |
  |---|---|---|---|---|
  | `qwen3.8-max-preview` | 235초 | 10,046 | ✅ 통과 | 느림. `귀법인`→`귀 법인` 처럼 원문보다 더 쪼갬 |
  | `qwen3.7-plus` | 69초 | 3,747 | **❌ 실패** | 글자를 바꿈 — 교정 용도로 쓰면 안 된다 |
  | **`qwen3.6-flash`** | **35초** | 4,119 | ✅ 통과 | **가장 빠르고 안전, 원문 어절 보존도 더 정확** |

  → 한국어 교정에는 `qwen3.6-flash`. 비싼 모델이 더 안전하지도 정확하지도 않았다.

- **출력을 반드시 기계 검사한다.** 교정 전후에서 공백을 모두 지운 문자열이 완전히 같아야 통과.
  `qwen3.7-plus` 가 이 검사에서 걸렸다 — 검사 없이 썼으면 정본 문서가 조용히 바뀐다.

## Do Not

- 공식 문서의 `cn-beijing` 을 그대로 믿지 말 것. 키 지역을 먼저 확인한다.
- **배치 자동화 금지는 실재한다.** 공식 개요 문서:
  "automation scripts, custom application backends, or any non-interactive batch call
  scenarios" 에서 API 호출은 **엄격히 금지**. 허용 도구는 Claude Code·Cursor·Qwen Code·
  Qoder·OpenClaw. 스크립트로 문서 수백 건을 돌리는 용도로 쓰지 말 것.
- 쿼터는 **5시간 창 + 7일 창**이 동시에 적용된다(Pro 40,000 크레딧/7일). 짧은 입력에도
  thinking 토큰이 수천 개라 소진이 빠르다 — 전량 처리 전에 표본으로 소요를 먼저 재라.
