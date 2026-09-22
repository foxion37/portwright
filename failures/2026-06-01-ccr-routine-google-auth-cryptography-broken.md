---
date: "2026-06-01"
service: ccr-routine
service_version: "claude.ai Routine container (linux, Python 3.11.15)"
status: active
distributable: true
---

# CCR 원격 Routine — google-auth(cryptography Rust 바인딩) 깨짐 → openssl JWT로 우회

- 날짜: 2026-06-01
- 환경: claude.ai 예약 Routine = 원격 CCR 컨테이너 (linux, Python 3.11.15)
- 도구: 서비스계정 + 도메인위임(DWD)으로 Gmail API 직접 발송

## 증상
원격 Routine에서 `pip install google-auth` 는 성공(인터넷 egress OK)하지만,
`from google.oauth2 import service_account` 임포트가 죽음:
`ModuleNotFoundError: No module named '_cffi_backend'` →
`cryptography.hazmat.bindings._rust` → `pyo3_runtime.PanicException`.
시스템 cryptography(Rust pyo3) 바인딩 손상 → google-auth/google-api-python-client 전부 사용 불가.

## 진짜 원인
CCR 샌드박스의 시스템 Python에 깔린 cryptography 패키지가 _cffi_backend를 못 올림.
google 라이브러리가 이에 의존 → import 단계에서 패닉.

## 해결 (검증됨, 2026-06-01 실제 발송 성공)
google-auth 쓰지 말고 **openssl로 RS256 JWT 수동 서명** → oauth2 token → Gmail API:
1. 키 JSON에서 private_key(PEM), client_email 추출.
2. header `{"alg":"RS256","typ":"JWT"}`, claim `{iss:client_email, sub:<impersonate user>, scope:"https://www.googleapis.com/auth/gmail.send", aud:"https://oauth2.googleapis.com/token", iat, exp}` → 각각 base64url.
3. `printf '%s' "$header.$claim" | openssl dgst -sha256 -sign key.pem | base64url` → 서명.
4. `curl -X POST https://oauth2.googleapis.com/token -d grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer -d assertion=$JWT` → access_token.
5. `curl -X POST https://gmail.googleapis.com/gmail/v1/users/me/messages/send -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"raw":"<base64url MIME>"}'`.
- base64url = `base64 | tr '+/' '-_' | tr -d '='`.

## 교훈
- 원격 CCR에서 RSA 서명/서비스계정 인증이 필요하면 **openssl CLI를 1순위로** 가정. (cryptography 휠은 못 믿음)
- 원격 Routine은 claude.ai 계정 커넥터를 자동 상속하지만, Gmail 커넥터는 **draft 전용(send 없음)** → 실제 발송은 서비스계정+DWD+openssl 경로.
- 검증법: 발송 결과를 Drive 문서로 남기게 하면 `RemoteTrigger get`이 출력을 안 줘도 로컬 Drive로 읽어 진단 가능.
