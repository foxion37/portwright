---
date: "2026-07-14"
service: xcode
service_version: "Xcode 26.6 (17F113)"
status: active
distributable: true
---

## 증상
소스 entitlement에는 macOS App Sandbox와 User Selected File Read/Write만 있었지만, 로컬 Release 앱의 signed entitlement에는 com.apple.security.get-task-allow가 추가돼 있었다.

## 진짜 원인
로컬 서명 과정이 기본 entitlement를 주입했다. 소스 entitlement 파일만 확인해서는 최종 서명 결과를 알 수 없다.

## 해결
정확한 Release 허용 목록이 필요한 app target에 CODE_SIGN_INJECT_BASE_ENTITLEMENTS = NO를 설정했다. 새 build-only DerivedData에서 Release를 다시 빌드하고 codesign -d --entitlements :- "$APP_PATH"로 두 권한만 남았는지 확인했다.

## 다음에 할 일
권한 검사는 소스 파일과 signed app을 모두 본다. Debug의 get-task-allow는 개발용으로 허용할 수 있지만, Release 기준이 엄격하면 base entitlement 주입을 끄고 서명 결과를 다시 검사한다.
