---
id: xcode
display_name: "Xcode and Apple Simulator"
version_tag: "Xcode 26.6 (17F113), iOS Simulator 26.5"
last_verified: "2026-07-14"
endpoint:
  type: cli
  server: "Xcode.app, xcodebuild, xcrun simctl"
human_steps:
  - "Xcode가 없으면 Mac App Store에서 최초 1회 설치한다."
  - "Apple 약관이나 macOS 권한 창처럼 본인 동의가 필요한 항목만 직접 승인한다."
agent_can:
  - "Xcode 선택 경로, SDK, 플랫폼 runtime, Simulator 상태를 점검한다."
  - "iOS runtime을 내려받고 사용 가능한 iPhone Simulator를 부팅한다."
  - "네이티브 프로젝트를 만들고 빌드, 테스트, 앱 설치, 실행, 번들 감사를 수행한다."
status: active
distributable: true
---

## 한 줄 요약
Xcode 설치 뒤의 환경 점검부터 macOS와 iPhone Simulator 실기동 검증까지 Agent가 맡고, User에게는 설치와 법적 동의처럼 사람만 할 수 있는 단계만 요청한다.

## 정답 절차
1. 먼저 xcode-select -p, xcodebuild -version, swift --version, xcodebuild -showsdks로 실제 선택된 Xcode와 SDK를 확인한다.
2. iOS runtime과 기기는 xcrun simctl list runtimes -j, xcrun simctl list devicetypes -j, xcrun simctl list devices available -j로 확인한다.
3. iOS runtime이 없으면 User에게 Xcode 설정 화면을 열라고 하지 말고 xcodebuild -downloadPlatform iOS를 먼저 실행한다. 완료 뒤 runtime 목록을 다시 확인한다.
4. 프로젝트 생성이 필요하면 Xcode의 네이티브 템플릿을 사용한다. 생성 직후 xcodebuild -project <PROJECT>.xcodeproj -list로 targets와 shared scheme을 확인하고, xcuserdata와 *.xcuserstate는 커밋 대상에서 뺀다.
5. Debug와 Release 각각에서 macOS, generic/platform=iOS Simulator, generic/platform=iOS의 -showBuildSettings를 확인한다. 지원 플랫폼, 배포 버전, Bundle ID, Team, entitlement, Catalyst, Apple Vision 호환 설정을 이름 그대로 검사한다.
6. 빌드는 작업별로 분리한 DerivedData에서 실행한다. 컴파일 확인과 별도로 실제 iPhone UDID를 부팅하고 xcrun simctl bootstatus "$IOS_UDID" -b 뒤 전체 테스트를 실행한다.
7. macOS는 정확한 빌드 산출물을 open -n "$APP_PATH"로 실행하고, 실행 중인 바이너리 경로와 SHA-256이 빌드 파일과 같은지 확인한다.
8. iPhone Simulator는 기존 앱을 종료하고 제거한 뒤 정확한 산출물을 xcrun simctl install로 설치한다. launch, get_app_container, 파일별 SHA-256 비교, simctl io screenshot 순서로 설치본과 화면을 검증한다.
9. 배포 번들 감사에는 테스트를 실행한 DerivedData를 쓰지 않는다. 새 build-only DerivedData에서 다시 빌드한 뒤 HTML, CSS, Markdown, Info.plist, package, signed entitlement를 검사한다.
10. 마지막으로 git status --short와 staged 파일 목록을 확인해 기존 dirty 파일, Xcode 사용자 상태, 증거 파일이 섞이지 않았는지 확인한다.
11. 같은 SwiftUI UI가 플랫폼별로 다른 접근성 형태를 낼 수 있다. macOS의 `Text`는 `label` 대신 `value`, SwiftUI 경고창은 `Alert` 대신 `Sheet`로 보일 수 있으므로 xcresult의 UI hierarchy를 확인하고 플랫폼 중립 도우미나 조건부 선택자를 쓴다.

## 하지 말 것
- iOS runtime 다운로드, Simulator 부팅, 앱 설치와 화면 캡처를 User에게 떠넘기지 않는다.
- generic Simulator 빌드만 통과하고 실제 iPhone Simulator 실행을 생략하지 않는다.
- SUPPORTED_PLATFORMS만 보고 Apple Vision 호환이 꺼졌다고 추정하지 않는다.
- 테스트가 주입된 앱 번들을 배포 앱처럼 감사하지 않는다.
- UDID, 사용자 경로, 계정 이름, 원본 로그를 Procedure나 Lesson에 저장하지 않는다.

## 관련 실패 기록
- failures/2026-07-14-xcode-ios-runtime-missing.md
- failures/2026-07-14-xcode-disable-vision-compatibility.md
- failures/2026-07-14-xcode-audit-clean-build-products.md
- failures/2026-07-14-xcode-prevent-release-debug-entitlement.md
