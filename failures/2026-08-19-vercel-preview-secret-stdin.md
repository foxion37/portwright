---
date: 2026-08-19
service: vercel
service_version: "Vercel CLI + REST API 2026-08"
status: active
distributable: true
---

## 무엇을 시도했나
팀 전용 자격으로 Preview-only server secret을 만들기 위해 CSPRNG 출력을 `vercel env add ... preview --sensitive --yes` stdin으로 직접 전달했다.

## 어떤 에러가 났나
CLI가 stdin 값을 받지 못해 실패했고 환경변수 write는 발생하지 않았다. 이어서 inline `node - <<HEREDOC` API 클라이언트에 random 출력을 pipe하려 했지만 heredoc이 stdin을 차지해 pipe가 끊겼다. API write는 발생하지 않았다.

## 진짜 원인
- 이 CLI 버전/연결에서 문서화된 env-add stdin surface가 값을 정상 수신하지 않았다.
- Node 프로그램 본문과 secret 양쪽에 stdin을 동시에 사용한 shell redirection 오류가 있었다. heredoc redirection이 pipe를 덮어쓴다.
- 기본 Vercel CLI 자격은 대상 팀 권한이 없지만, 기존 팀 전용 자격은 별도 config에 정상 보관돼 있었다.

## 고친 방법
1. 기존 팀 전용 auth file의 token을 출력하지 않고 프로세스 메모리에서 사용한다.
2. 공식 `POST /v10/projects/{id}/env?teamId=...&upsert=true` endpoint를 사용한다.
3. 비밀이 없는 API client 코드를 임시 `.cjs`에 만든다.
4. `openssl rand -base64 64 | node client.cjs`로 최소 48 random bytes를 직접 pipe한다.
5. body는 `type=encrypted`, `target=[preview]`로 제한한다.
6. write 뒤 env 목록에서 key 존재와 Preview-only scope만 확인하고 value는 읽지 않는다.
7. 임시 스크립트와 응답 파일을 삭제한다.
