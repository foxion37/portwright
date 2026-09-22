---
date: "2026-06-20"
service: google-drive
service_version: "Drive API v3 / service-account upload to Shared Drive"
status: active
distributable: true
---

# Google Drive 서비스계정 업로드 403 accessNotConfigured — Drive API 미활성

## 증상

서비스계정 키 + 공유드라이브 공유까지 다 했는데 첫 업로드(`files().create(... supportsAllDrives=True)`)가:

```text
HttpError 403 ... "Google Drive API has not been used in project <NUM> before or it is disabled."
domain: usageLimits, reason: accessNotConfigured
```

## 진짜 원인

SA 인증·키·폴더 공유는 정상이지만 **GCP 프로젝트에 Drive API가 사용 설정 안 됨**. 인증이 통과해 API 호출까지 도달하므로 "권한/공유 문제"로 오진하기 쉬우나, `accessNotConfigured` = API 자체가 꺼진 것.

## 해결 (검증됨 2026-06-20)

1. Drive API 사용 설정: 콘솔 `https://console.developers.google.com/apis/api/drive.googleapis.com/overview?project=<NUM>` → Enable, **또는** `gcloud --account=<owner> services enable drive.googleapis.com --project=<id>`.
   - **SA 자신은 못 켬**(serviceUsageAdmin + Service Usage API 필요). 프로젝트 소유 user 계정으로.
   - `gcloud`가 비대화형에서 `Reauthentication failed`면 user 가 `gcloud auth login` 재인증 필요 → 그 후 agent 가 enable 가능.
2. 활성화 후 몇 분 전파 지연 가능 → 업로드 재시도(20s 백오프 몇 회).

## 부수 교훈

- 업로드된 파일의 webViewLink 가 **xlsx 면 `docs.google.com/spreadsheets/d/<id>`**(`drive.google.com` 아님) — "Drive 링크 아님"으로 오탐 말 것. Drive 파일 맞음.
- 공유드라이브(driveId `0A...`)에서 SA의 **영구 delete 가 404** 날 수 있음 → `files().update(trashed=True, supportsAllDrives=True)` 휴지통 이동으로 폴백.
- google-auth/cryptography 는 로컬 mac(정상 venv)에선 import OK. (CCR 원격 컨테이너 깨짐 사례와 구분.)
