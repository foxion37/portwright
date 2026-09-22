---
date: "2026-07-14"
service: xcode
service_version: "Xcode 26.6 (17F113)"
status: active
distributable: true
---

## 증상
Debug 앱 번들 감사에서 unit-test plugin, 테스트용 Markdown fixture, testmanager 관련 임시 entitlement가 발견돼 배포 번들이 오염된 것처럼 보였다.

## 진짜 원인
xcodebuild test는 테스트 실행을 위해 host app에 test bundle을 넣고 서명을 다시 만든다. 테스트가 끝난 DerivedData의 앱은 순수한 build 산출물과 구조와 entitlement가 다르다.

## 해결
별도의 새 DerivedData에서 xcodebuild ... build만 실행했다. 그 앱으로 HTML, CSS, Markdown, Info.plist, package, signed entitlement를 다시 감사해 테스트 주입 항목과 실제 앱 항목을 분리했다.

## 다음에 할 일
테스트 결과와 배포 번들 감사는 같은 DerivedData를 공유하지 않는다. 테스트에는 test용 경로를 쓰고, 최종 번들 감사와 수동 실행에는 새 build-only 경로를 쓴다.
