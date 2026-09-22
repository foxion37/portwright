---
date: "2026-09-03"
service: google-workspace
service_version: "Sheets API v4 via google-api-python-client and httplib2"
status: active
distributable: true
---

# 대량 Sheets 서식 요청에서 연결이 끊김

## 증상

Google Sheet 서식 변경 요청 468건을 하나의 `spreadsheets.batchUpdate` 본문으로 보내자 `BrokenPipeError`가 발생했다. 같은 API client로 60건씩 나눈 첫 재시도도 응답을 읽는 중 같은 오류가 났다.

## 원인

큰 요청 본문과 실패 뒤 남은 `httplib2` 연결을 함께 재사용한 것이 원인이었다. 요청 자체의 Sheets 규칙 오류는 아니었다.

## 진짜 원인

본문의 원인 분석에 따르면 큰 요청 본문과 실패 뒤 남은 `httplib2` 연결을 재사용한 것이 문제였다. 새 API client와 25건 단위 배치로 총 468개 요청을 처리한 결과가 해결 근거다.

## 검증된 해결

1. `build('sheets', 'v4', ...)`로 새 API client를 만든다.
2. 서식 요청을 25건씩 나누어 순서대로 `batchUpdate`한다.
3. 서식 요청은 같은 값을 다시 적용해도 결과가 같은 형태로 구성해 재시도를 안전하게 만든다.

이 방식으로 19개 배치, 총 468개 요청과 응답을 모두 처리했다.
