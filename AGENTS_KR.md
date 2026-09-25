# AGENTS_KR.md — Portwright 작업 규칙

이 저장소를 고치기 전에 먼저 읽으세요. AI 에이전트와 사람 모두에게 적용됩니다. 사람은 [CONTRIBUTING.md](CONTRIBUTING.md)부터 봐도 됩니다.

영문 사본: [AGENTS.md](AGENTS.md). 한쪽을 고치면 같은 변경에서 다른 쪽도 함께 고칩니다.

## 이 저장소가 무엇인가

Portwright는 에이전트가 어떤 도구를 건드리기 **전에** 그 도구에 대한 짧은 메모를 읽게 만듭니다. 도구를 대신 실행하지 않고, 자격증명을 갖지 않으며, 호출 경로에 끼어들지 않습니다.

- `services/<도구>.md` — 지금 맞는 절차(Procedure)
- `failures/<날짜>-<도구>-<슬러그>.md` — 원인이 확인된 교훈(Lesson)
- `profiles/<프로젝트>.md` — 어느 계정·비밀 저장소·데이터베이스에 속한 디렉터리인지. 본질적으로 개인 정보라 여기에는 하나도 포함되지 않습니다.
- `lib/`, `bin/portwright`, `tests/` — 그 메모를 읽고 검증하고 라우팅하는 동작부

게이트웨이가 아니고, 정책 엔진이 아니고, 상주 서버가 아니고, 감사 파이프라인이 아니며, 세상 모든 도구를 미리 적어 둔 레지스트리도 아닙니다.

## 이 트리는 생성물입니다

이 저장소는 비공개 작업 저장소에서 단방향으로 내보낸 결과입니다. **여기에 직접 한 커밋은 다음 내보내기 때 덮입니다.**

의도한 설계입니다. 원본 저장소에는 절대 공개되면 안 되는 개인 메모가 함께 있어서, 게시는 미러링이 아니라 걸러낸 빌드입니다. [역할과 범위](docs/roles-and-scope_KR.md) 및 [CONTRIBUTING.md](CONTRIBUTING.md)를 참고하세요. M2에 계획된 Hub MCP 제출 경로가 생기기 전에는 이 공개 저장소에서 교훈 기여를 받지 않습니다. GitHub Issue/PR의 자유 본문으로 교훈을 보내지 마세요.

## 절대 규칙 2개

1. **어떤 파일에도 비밀값을 넣지 않습니다.** 메모, 코드, 예시, 테스트, 커밋 메시지 어디에도 안 됩니다. 이름만 적습니다(`GITHUB_TOKEN`, 1Password 항목 이름 등). 민감한 값은 `[REDACTED]`로 바꿉니다.
2. **`human_steps`에는 사람만 할 수 있는 일만 적습니다.** CLI·MCP 서버·기존 연결로 처리할 수 있는데 "토큰 만들어서 붙여 넣으세요"라고 하는 것이 바로 이 프로젝트가 없애려는 안티패턴입니다. 피할 수 있는 사람 단계는 결함입니다.

## 메모 계약

`services/_TEMPLATE.md` 또는 `failures/_TEMPLATE.md`에서 시작하세요. 필수 frontmatter가 거기 정의돼 있습니다.

- 절차 키: `id`, `display_name`, `version_tag`, `last_verified`, `endpoint{type,server}`, `human_steps[]`, `agent_can[]`, `status`, `distributable`. 선택: `profile_ids`, `freshness_evidence.url`
- 교훈 키: `date`, `service`, `service_version`, `status`. 파일명: `YYYY-MM-DD-<service>-<slug>.md`
- `version_tag`는 다음 에이전트가 현재 문서에서 그대로 재현할 수 있는 문자열이어야 합니다(`clasp 3.3.0`). 신선도가 문자열을 그대로 비교하기 때문입니다.
- `distributable: false`는 그 메모가 각자 컴퓨터의 gitignore된 `_private/` 루트에 남는다는 뜻입니다. 그런 메모는 이 저장소에 오지 않습니다.
- `bin/portwright check`가 통과해야 합니다. 초안을 포함한 모든 메모를 검사합니다.

## 메모는 초안이 먼저입니다

파일을 손으로 옮기지 마세요. 아래 흐름을 씁니다.

```sh
bin/portwright memory draft procedure <service-id>
bin/portwright memory draft lesson <service-id> <slug>
bin/portwright memory review <draft-path>
bin/portwright memory promote <draft-path>
```

`review`는 비밀로 의심되는 문자열을 검사하고, `promote`는 계약을 검증하고 원인이 확정되지 않은 초안을 거부하며 승격된 교훈을 해당 절차에 연결합니다. 원인이 추측이면 `unconfirmed`라고 쓰고 승격하지 않습니다.

## 없으면 그때 도출합니다. 라이브러리를 손으로 채우지 않습니다

절차는 필요해서 캐시된 것이지 미리 적어 둔 것이 아닙니다. 쓰지도 않는 도구의 절차를 미리 작성하지 마세요.

메모가 없거나 낡았으면 그 도구의 공식 문서에서 현재 절차를 도출한 뒤 캐시합니다. 공식 문서와 실제 도구가 다르면 직접 확인한 쪽을 믿고 그 사실을 메모에 적습니다. 현재 경로를 확인하지 않은 설정 단계를 사용자에게 안내하지 않습니다.

## 약하게 바꾸면 안 되는 의미들

- `unknown`은 절대 `fresh`가 아닙니다. 근거가 없으면 `unknown`과 "먼저 확인"입니다.
- 버전 일치로 나온 `fresh`는 "버전이 같다"까지만 주장합니다. 모든 단계를 다시 증명했다는 뜻이 아닙니다. 코드와 문서에서 이 표현을 정직하게 유지하세요.
- 등급(`auto|confirm|forbid`)은 **조언**입니다. 집행은 각 Client의 권한 프롬프트가 합니다. 집행·가로채기·게이트웨이를 넣는 변경은 `ARCHITECTURE.md`의 결정 D1에 어긋나며 받지 않습니다.
- 규칙에 안 맞거나 모르면 `confirm`으로 떨어집니다. 모르면 물어보는 쪽으로 실패합니다.

## 제약

- Python 표준 라이브러리만 씁니다. 런타임 의존성, 빌드 시스템, 상주 프로세스를 추가하지 않습니다.
- 영문 문서와 `_KR` 짝은 한 커밋에서 함께 고칩니다.
- 동작은 결정적이고 오프라인에서 검증 가능해야 합니다. 모델 판단은 보조일 뿐 유일한 경로가 되면 안 됩니다.

## 된다고 말하기 전에 검증합니다

```sh
python3 -m unittest discover -s tests -q
bin/portwright check
```

둘 다 통과해야 합니다. 테스트는 있을 법한 버그가 실제로 그 테스트를 깨뜨릴 때만 값을 합니다.

## 받지 않는 것

개인 메모, 프로파일, 특정 컴퓨터 경로, 계정 핸들, 조직명, 어떤 형태든 자격증명, 새 의존성, 게이트웨이나 집행 기능, 쓰지도 않는 도구의 절차 라이브러리.
