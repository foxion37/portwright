---
date: 2026-08-15
service: figma
service_version: "official hosted use_figma, 2026-08-15"
status: active
distributable: true
---

# `saveVersionHistoryAsync` is unsupported in hosted `use_figma`

## Failure

Calling the Plugin API method in a Slides validation script returned:

```text
Error: in saveVersionHistoryAsync: "saveVersionHistoryAsync" is not a supported API
```

The failed script was atomic, so none of its operations ran.

## 진짜 원인

호스팅된 `use_figma` 실행 환경은 `saveVersionHistoryAsync`를 지원하지 않았다. 본문의 `"saveVersionHistoryAsync" is not a supported API` 응답이 이 제한을 명시한다.

## Correct handling

- Do not call `figma.saveVersionHistoryAsync()` through hosted `use_figma`.
- Keep final layout validation in a separate read-only script.
- Use the Figma UI for named version-history saves when needed.
