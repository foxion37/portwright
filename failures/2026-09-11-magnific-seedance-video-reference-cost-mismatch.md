---
date: "2026-09-11"
service: magnific
service_version: "bytedance-seedance-pro-2.5 observed 2026-09-11"
status: active
distributable: true
---

# Magnific Seedance video-reference cost mismatch

## Observed

- Model: `bytedance-seedance-pro-2.5`
- Output request: 1080p, 9:16, 20 seconds
- Inputs: one video reference plus six image references
- `simulate_cost` reported an exact cost of 10,800 credits.
- The accepted generation receipt reported 21,600 credits.

## 진짜 원인

이 요청에서는 `simulate_cost`의 예상 비용이 실제 생성 접수 영수증의 비용과 일치하지 않아 최종 청구액의 근거로 사용할 수 없었다. 본문에 기록된 예상 비용은 10,800 credits이고 접수 영수증은 21,600 credits였다. 두 산정 결과가 달라진 서버 내부 원인은 이 기록만으로 확인할 수 없다.

## Lesson

Do not treat `simulate_cost` as the final payable amount for Seedance jobs that include a video reference. The accepted generation receipt is the authoritative charge evidence. Before submitting, tell the user that video-reference pricing may be higher than the simulation; after submission, compare the receipt and disclose any mismatch immediately.

Do not retry or create a second paid generation solely because the simulated and charged amounts differ.
