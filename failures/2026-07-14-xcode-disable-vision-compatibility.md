---
date: "2026-07-14"
service: xcode
service_version: "Xcode 26.6 (17F113)"
status: active
distributable: true
---

## 증상
프로젝트가 iphoneos iphonesimulator macosx만 지원하도록 설정됐는데도 실제 iOS build settings에는 SUPPORTS_XR_DESIGNED_FOR_IPHONE_IPAD=YES가 남아 있었다.

## 진짜 원인
Xcode 템플릿의 Apple Vision 호환 실행 설정은 SUPPORTED_PLATFORMS와 별개다. 따라서 xros target이 없어도 iPhone과 iPad 앱의 Apple Vision 호환 실행이 기본으로 켜질 수 있다.

## 해결
앱 target의 Debug와 Release에 SUPPORTS_XR_DESIGNED_FOR_IPHONE_IPAD = NO를 명시했다. generic/platform=iOS의 -showBuildSettings에서 Debug와 Release가 모두 NO인지 확인한 뒤 빌드, 테스트, 실제 실행을 다시 검증했다.

## 다음에 할 일
macOS와 iOS만 지원해야 하는 프로젝트는 SUPPORTED_PLATFORMS, SUPPORTS_MACCATALYST, SUPPORTS_XR_DESIGNED_FOR_IPHONE_IPAD를 함께 확인한다.
