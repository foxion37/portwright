---
date: 2026-09-10
service: notion
service_version: REST API 2026-03-11
status: active
distributable: true
---

## 무엇을 시도했나

기존 페이지의 블록 사이에 내용을 끼워 넣기 위해 `PATCH /v1/blocks/{page_id}/children` 본문에 `after`를 넣고, 교체된 초안 페이지를 정리하기 위해 `PATCH /v1/pages/{page_id}` 본문에 `archived: true`를 넣었다.

## 어떤 에러가 났나

두 요청 모두 `400 validation_error`를 반환했다. API는 `after`와 `archived`가 요청 본문에 없어야 한다고 명시했다.

## 진짜 원인

`Notion-Version: 2026-03-11`에서 확인한 현재 요청 스키마가 예전 예제의 필드를 받지 않는다. 블록 추가 요청에는 `after`를 사용할 수 없었고, 페이지를 휴지통으로 보내는 필드는 `archived`가 아니라 `in_trash`였다.

## 고친 방법 (다음엔 이대로)

- 중간 또는 앞쪽 삽입이 필요하면 완성된 블록 순서로 새 페이지를 먼저 생성하고 검증한다.
- 검증이 끝난 뒤 교체 대상 페이지에 `PATCH /v1/pages/{page_id}`와 `{"in_trash": true}`를 보낸다.
- 초안을 먼저 지우지 않는다. 새 페이지의 제목, 부모, 전체 블록 수와 필요한 블록 타입을 확인한 뒤 기존 초안을 휴지통으로 보낸다.
- API 버전이 바뀌면 이 기록을 다시 검증한다.
