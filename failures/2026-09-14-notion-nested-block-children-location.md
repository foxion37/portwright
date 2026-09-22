---
date: 2026-09-14
service: notion
service_version: REST API 2026-03-11
status: active
distributable: true
---

# Notion 중첩 블록의 children 위치

## 무엇을 시도했나

`POST /v1/pages`로 검토용 원고를 만들면서 토글 블록을 다음처럼 보냈다.

```json
{
  "type": "toggle",
  "toggle": { "rich_text": [], "color": "default" },
  "children": []
}
```

## 어떤 에러가 났나

`400 validation_error`가 반환됐고 `body.children[1].children should be not present`라고 나왔다. 페이지는 생성되지 않았다.

## 진짜 원인

Notion API `2026-03-11`의 중첩 자식은 블록 최상위가 아니라 블록 타입 객체 안에 둔다. 공식 Block 문서의 토글 예제도 `toggle.children`을 사용한다. 같은 규칙이 `paragraph`, `to_do`, `bulleted_list_item` 등 자식을 지원하는 블록에 적용된다.

## 다음에는 이렇게 한다

```json
{
  "type": "toggle",
  "toggle": {
    "rich_text": [],
    "color": "default",
    "children": []
  }
}
```

중첩이 꼭 필요하지 않으면 검토 체크리스트를 평면 `heading_3`과 `to_do` 블록으로 만들어도 된다. 생성 전 요청 본문에서 각 `children`이 대응하는 타입 객체 안에 있는지 검사한다.

공식 근거: https://developers.notion.com/reference/block#toggle-blocks
