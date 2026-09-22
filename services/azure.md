---
id: azure
display_name: "Microsoft Azure"
version_tag: "Azure AI Speech and Artifact Signing docs, 2026-09-02"
last_verified: "2026-09-02"
endpoint:
  type: cli
  server: "https://portal.azure.com"
human_steps:
  - "Choose or approve a paid Azure subscription and billing method. Never send payment details in chat."
  - "Provide legal organization identity and an authorized representative for Artifact Signing Public Trust validation."
  - "Complete any Microsoft identity-document or organization-document challenge in the Azure portal."
agent_can:
  - "Use an existing authenticated Azure session or az CLI context without printing credentials."
  - "Create and inspect Korea Central Speech resources after billing authorization."
  - "Register Microsoft.CodeSigning and prepare Artifact Signing account, validation, and certificate-profile requests."
status: active
distributable: true
---

## 한 줄 요약
Azure의 한국어 음성 처리와 공개 코드 서명은 유료 구독, 한국 리전, 기관 검증 상태를 먼저 확인하고 시크릿을 출력하지 않은 채 진행한다.

## 정답 절차 초안
1. `az account show` 또는 Portal에서 현재 tenant, subscription 상태, billing offer를 확인한다. 카드, key, token은 출력하거나 기록하지 않는다.
2. Speech는 Korea Central(`koreacentral`)의 전용 `SpeechServices` S0 resource를 사용한다. Portal 배포 성공 화면과 secret을 제외한 resource ID, kind, SKU, location을 증거로 남긴다.
3. Batch STT는 system-assigned managed identity와 `Storage Blob Data Reader`를 우선한다. Blob anonymous access와 account-key access를 끄고 Korea Central storage를 쓴다.
4. Artifact Signing은 paid subscription에서 `Microsoft.CodeSigning` provider를 등록한다. 한국 기관의 공개 설치기는 Public Trust를 선택하며 Private Trust와 Public Trust Test를 대체재로 쓰지 않는다.
5. Organization validation에 법인명, 소유 도메인 이메일 2개, 사업자 식별자, 주소, 정부 신분증과 같은 대표자명을 입력한다. 이메일 링크는 7일 안에 확인한다.
6. 신청 뒤 portal의 request/account/profile ID, 상태, 접수 시각만 기록한다. 공식 안내상 identity validation 예상 기간은 1~20 영업일이며 문서 요청 시 더 길어질 수 있다.

## 하지 말 것
- Azure key, connection string, payment card, identity document를 채팅, 로그, 저장소에 남기지 않는다.
- Speech batch가 불가능한 F0를 운영 자격으로 기록하지 않는다.
- Public Trust가 필요한 배포에 Private Trust를 사용하지 않는다.
- 사용자의 결제 권한이나 법적 대표 권한을 추정하지 않는다.

## 출처
- https://learn.microsoft.com/en-us/azure/ai-services/speech-service/regions
- https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-services-private-link
- https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-configure-azure-ad-auth
- https://learn.microsoft.com/en-us/azure/ai-services/speech-service/batch-transcription-audio-data
- https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart
- https://learn.microsoft.com/en-us/azure/artifact-signing/concept-trust-models
- https://learn.microsoft.com/en-us/azure/artifact-signing/faq

## 관련 실패 기록
- 없음
