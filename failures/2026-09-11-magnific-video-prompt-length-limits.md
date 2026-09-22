---
date: 2026-09-11
service: magnific
service_version: "Native Magnific MCP, Seedance 2.5 catalog observed 2026-09-11"
status: active
distributable: true
---

## What was attempted

Plan and generate a reference-guided, multi-shot promotional video through the enabled Magnific MCP connector.

## Observed errors

- `video_plan` rejected a `styleHint` longer than 200 characters.
- Multi-shot video generation rejected a clip whose combined shot prompts contained 10,437 characters, exceeding the 10,000-character limit.

## Confirmed cause

The short style hint has its own validation limit. The Seedance prompt limit also applies to the combined `multi_prompt` text for each clip, not independently to each shot.

## 진짜 원인

`styleHint`의 200자 제한과 클립 전체 `multi_prompt`의 10,000자 제한을 초과했다. 본문에 기록된 10,437자 요청과 검증 오류는 제한이 개별 샷이 아니라 클립의 합산 프롬프트에 적용됨을 보여준다.

## Working correction

- Keep `styleHint` at or below 200 characters and put detailed instructions in `prompt`.
- Count the combined multi-shot prompt text before submission. Reduce repeated continuity instructions until the total is below 10,000 characters.
- Re-simulate cost after changes. The corrected requests were accepted and the resulting clips completed.
- Read the currently exposed tool schema and model catalog before reuse; the primary connector and app wrapper may expose different request envelopes.
- Do not confuse successful generation with visual compliance. Sample actual frames and inspect anatomy, text, scale and spatial continuity before assembly.

No account identifiers, signed URLs or credentials are needed for this lesson.
