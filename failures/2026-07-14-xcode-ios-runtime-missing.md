---
date: "2026-07-14"
service: xcode
service_version: "Xcode 26.6 (17F113)"
status: active
distributable: true
---

## 증상
Xcode 설치는 끝났지만 simctl에 사용 가능한 iOS runtime과 부팅 가능한 iPhone이 없어 실제 Simulator 테스트를 시작할 수 없었다.

## 진짜 원인
Xcode 앱만 설치됐고 iOS 플랫폼 runtime은 아직 내려받지 않은 상태였다. xcrun simctl list runtimes -j에서 사용 가능한 iOS runtime이 없음을 확인했다.

## 해결
xcodebuild -downloadPlatform iOS를 실행한 뒤 runtime과 device 목록을 다시 확인했다. 사용 가능한 iPhone UDID를 부팅하고 xcrun simctl bootstatus "$IOS_UDID" -b가 끝난 뒤 테스트를 실행했다.

## 다음에 할 일
Xcode 설치 완료를 Simulator 준비 완료로 보지 않는다. Apple 앱 작업을 시작할 때는 SDK, runtime, device를 따로 점검하고, 빠진 iOS runtime은 Agent가 CLI로 설치한다.
