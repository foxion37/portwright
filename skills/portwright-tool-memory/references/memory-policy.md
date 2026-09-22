# Portwright Memory Policy

## Service Draft

Create `services/_drafts/<id>.md` with:

```markdown
---
id: <service-id>
display_name: <Service Name>
version_tag: "<official docs/API/MCP version or date>"
last_verified: "<YYYY-MM-DD>"
endpoint:
  type: mcp
  server: "<server/url/name>"
human_steps:
  - <only steps a human must do>
agent_can:
  - <steps the agent can do after connection>
status: active
---

## 한 줄 요약
<one sentence>

## 정답 절차 초안
1. <verified or docs-derived step>

## 하지 말 것
- <manual work agents must not offload>

## 관련 실패 기록
- <failure draft path if any>
```

## Failure Draft

Create `failures/_drafts/<YYYY-MM-DD>-<service>-<slug>.md` with:

```markdown
---
date: <YYYY-MM-DD>
service: <service-id>
service_version: "<version/date>"
status: active
---

## 무엇을 시도했나
<agent action, summarized>

## 어떤 에러가 났나
<short error summary, redacted>

## 진짜 원인
<confirmed root cause only; write "unconfirmed" if not proven>

## 고친 방법 (다음엔 이대로)
<corrected procedure>
```

## Redaction Rules

Before writing:

- Replace secrets/tokens/API keys/OAuth codes/cookies/private keys with `[REDACTED]`.
- Summarize logs; do not paste full raw logs.
- Do not write user personal data unless essential and anonymized.
- Do not promote a draft when root cause is guessed.

## Promotion Rule

A draft can move to durable memory only when:

1. The fix was actually run or otherwise verified.
2. Root cause is confirmed.
3. The related `services/<id>.md` is updated if the failure changes the correct procedure.
4. No secret or PII remains.
