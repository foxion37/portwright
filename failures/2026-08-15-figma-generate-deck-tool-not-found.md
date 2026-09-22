---
date: 2026-08-15
service: figma
service_version: "official hosted Figma MCP, 2026-08-15"
status: active
distributable: true
---

# Figma `generate_deck` can be listed but unavailable on the server

## Failure

The client exposed a `figma_generate_deck` tool schema, but calling it returned:

```text
McpServerError: Tool generate_deck not found
```

The failure created no file and made no changes.

## 진짜 원인

클라이언트가 노출한 도구 스키마와 서버에서 호출 가능한 도구가 일치하지 않았다. 본문의 `Tool generate_deck not found` 응답이 서버에서 해당 도구를 찾지 못했음을 보여준다.

## Verified workaround

1. Call `create_new_file` with `editorType: "slides"`.
2. Use the returned file key with `use_figma`.
3. Create rows and slides incrementally with `figma.createSlideRow()` and `figma.createSlide()`.
4. Populate and validate each slide through `use_figma` with `skillNames: "figma-use,figma-use-slides"`.

Do not retry `generate_deck` against the same hosted server after this error.
