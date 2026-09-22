---
id: codesign
display_name: "Windows public code signing"
version_tag: "Microsoft Artifact Signing and DigiCert docs, 2026-09-02"
last_verified: "2026-09-02"
endpoint:
  type: cli
  server: "https://portal.azure.com"
human_steps:
  - "Approve the paid subscription or CA purchase and accept the applicable service agreement."
  - "Provide exact legal nonprofit identity, registration evidence, verified organization contacts, and government-ID verification."
  - "Respond to document, email, or verified-phone challenges from Microsoft or the certificate authority."
agent_can:
  - "Prepare and inspect Microsoft Artifact Signing resources after Azure authorization."
  - "Record request IDs, non-secret status, and published response windows."
  - "Verify a signed artifact's embedded certificate, hash, timestamp, and publisher identity."
status: active
distributable: true
---

## 한 줄 요약
한국 비영리의 공개 Windows 설치기는 Microsoft Artifact Signing Public Trust를 기본으로 신청하고, 실제 EV가 필수일 때만 별도 CA 경로를 쓴다.

## 정답 절차 초안
1. 기본 경로는 Artifact Signing Public Trust다. 한국 기관이 지원되고 Microsoft가 private key를 관리하며 Windows 공개 신뢰 체인을 제공한다. Private Trust와 Public Trust Test는 공개 배포 증거가 아니다.
2. paid Azure subscription에서 `Microsoft.CodeSigning` provider를 등록하고 Korea Central의 Artifact Signing account를 만든다. 계정 생성부터 과금되므로 구매 권한을 먼저 확인한다.
3. Identity validations > Organization > New Identity > Public에서 정확한 법인명, website, 소유 도메인의 서로 다른 primary/secondary email, 사업자 식별자, 주소, 정부 신분증과 같은 대표자명을 제출한다.
4. 접수 ID, `In Progress` 또는 `Action Required` 상태, 접수 시각, 확인 메일만 보존한다. 공식 예상은 1~20 영업일이며 추가 문서 요청 시 더 길어질 수 있다.
5. 검증 완료 뒤 Public Trust certificate profile을 만든다. account/profile/validation ID와 상태를 기록하되 private credential은 없다.
6. Artifact Signing은 EV certificate를 발급하지 않는다. EV가 명시적 제출 요건이면 DigiCert 같은 CA의 EV Code Signing을 별도 신청하며, validated organization/contact, FIPS 140-2 L2급 HSM 또는 관리형 key, 결제와 계약 동의가 필요하다.
7. 최종 증거는 signed binary의 embedded public certificate, timestamp, publisher identity, artifact SHA-256, 검증 로그다.

## 하지 말 것
- 무료, trial, sponsored Azure subscription을 Artifact Signing 자격으로 기록하지 않는다.
- checksum만 있는 artifact를 서명판으로 부르지 않는다.
- 신분증, 결제 정보, certificate private key, HSM secret을 채팅이나 저장소에 남기지 않는다.
- Artifact Signing Public Trust를 EV로 설명하지 않는다.

## 출처
- https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart
- https://learn.microsoft.com/en-us/azure/artifact-signing/concept-trust-models
- https://learn.microsoft.com/en-us/azure/artifact-signing/faq
- https://azure.microsoft.com/en-us/pricing/details/artifact-signing/
- https://docs.digicert.com/en/certcentral/order-and-manage-certificates/request-certificates/request-a-code-signing-or-ev-code-signing-certificate/request-code-signing-certificate.html

## 관련 실패 기록
- 없음
