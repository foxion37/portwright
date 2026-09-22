---
date: "2026-09-02"
service: 1password
service_version: "1Password CLI 2.35.0"
status: active
distributable: true
---

## 증상
`op item list --format json`의 `urls[].href` 메타데이터에 OAuth callback query/fragment가 그대로 포함되어, 액세스 토큰이나 일회성 코드가 메타데이터 출력에 섞일 수 있었다.

## 진짜 원인
1Password의 URL 필드는 사용자가 저장한 전체 URL을 반환한다. 항목 본문의 secret field를 읽지 않더라도 URL query/fragment 자체가 secret일 수 있다.

## 해결
인벤토리와 중복 분석에서는 원본 JSON을 출력하거나 장기 보관하지 않는다. 수집 즉시 `urls[].href`를 origin/path까지만 남기도록 query와 fragment를 제거하고, 원본 임시 파일은 삭제한다. 출력에는 item id, title, vault, category, account label, sanitized URL, created_at, updated_at만 허용한다.

## 다음에 할 일
- `op item list` 결과를 바로 화면에 출력하지 않는다.
- URL은 항상 `?`와 `#` 뒤를 제거한 뒤 분석한다.
- 콜백 URL이 저장된 항목은 별도 보안정리 후보로 표시한다.
- 원본 메타데이터는 작업 완료 즉시 삭제한다.
