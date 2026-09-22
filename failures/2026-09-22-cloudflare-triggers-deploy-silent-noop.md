---
date: 2026-09-22
service: cloudflare
service_version: wrangler 4.128.0
status: active
profile_id:
distributable: true
---

## 무엇을 시도했나

Cron Trigger를 가진 Worker의 예약 주기를 잠시 1분으로 바꿔 실제 실행을 관측하려고 `wrangler triggers deploy --triggers '* * * * *'` 를 실행했다.

## 어떤 에러가 났나

명령은 종료 코드 0으로 성공했고 오류 메시지도 없었다. 그러나 18분 동안 예약 실행이 한 번도 일어나지 않았다. `wrangler tail` 에 cron 이벤트가 없었고, 핸들러가 기록하는 R2 상태 객체도 생성되지 않았다. `wrangler deployments status` 는 버전만 출력하고 붙어 있는 예약 주기를 보여주지 않아, 성공 여부를 확인할 수 없었다.

## 진짜 원인

`wrangler triggers deploy` 는 `wrangler versions upload` 흐름을 위한 실험적 명령이다. 일반 `wrangler deploy` 로 올린 Worker에서는 예약 주기를 실제로 붙이지 않으면서도 성공으로 끝난다. 종료 코드는 적용 여부의 증거가 아니다.

## 고친 방법 (다음엔 이대로)

- 예약 주기는 바꿀 때도 되돌릴 때도 항상 `wrangler deploy --triggers '<cron>'` 으로 값을 명시한다.
- 인자 없는 `wrangler deploy` 는 설정 파일의 `triggers.crons` 로 되돌려 주지 않았다. `schedule: 17 3 * * *` 를 출력했는데도 이전 `--triggers` 로 붙은 1분 주기가 그대로 살아 있어서 22분 동안 매분 실행됐다. 같은 값을 `--triggers` 로 다시 명시하고 나서야 멈췄다.
- 출력의 `schedule:` 줄은 적용 의도를 보여줄 뿐 실제 상태의 증거가 아니다. 적용과 중단은 모두 핸들러의 부수 효과로 확인한다. 이 경우 R2 상태 객체의 `checked_at` 이 갱신되는지, 그리고 되돌린 뒤 5분 넘게 멈춰 있는지를 확인했다.
- 관측을 위해 주기를 임시로 줄였다면, 끝난 뒤 원래 값으로 명시 적용하고 실제로 멈췄는지까지 확인한 다음 작업을 마친다.
- 대시보드에서 Worker가 "존재하지 않는다"고 나오면 삭제가 아니라 브라우저 세션이 다른 계정을 보고 있을 수 있다. CLI 프로필로 먼저 대조한다.
