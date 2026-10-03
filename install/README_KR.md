# portwright — 설치 (한국어)

영어 원본은 [`README.md`](README.md)입니다. 두 문서는 같은 내용을 유지합니다.
portwright는 앱이나 서버가 아니라 **마크다운 노트 + 스킬 2개 + 훅 + CLI**입니다.
공개 저장소에는 시작용 Procedure(`services/`, 예: `github`)와 Lesson(`failures/`)이
함께 들어 있고, 내 노트는 내 컴퓨터의 클론 안에서 자랍니다.

## 요구 사항

- `git`(clone, `portwright update`)과 `bash`(`bin/portwright` 실행기).
- `python3`, **3.9 이상 지원**, 실행하는 프로세스의 `PATH`에 있어야 합니다. 검증 범위:
  upstream CI는 3.12, 격리 venv 스모크(clone, `check`, `preflight`)는 3.14.6에서
  실행했습니다. 3.9~3.11은 지원 하한이지만 체계적으로 시험하지 않았고, 3.9 미만은
  지원하지 않습니다.
- **로컬 CLI는 `pip install`도 패키지도 외부 의존성도 없습니다.** 이 문서의 명령은
  표준 라이브러리만 씁니다. 별개로 게시/Hub 운영 도구(`scripts/`, `hub/`)는 설치에
  포함되지 않으며 Node.js나 YAML 모듈이 필요할 수 있습니다. 가상환경은 선택입니다.
- macOS 또는 Linux와 POSIX 셸. Windows는 검증하지 않았습니다.
- 기본 로컬 워크플로(`check`, `preflight`, `memory`, `client`)는 자격 증명도 네트워크도
  필요 없습니다. `update`는 `origin`에 대한 `git` 접근이 필요합니다. Hub(`hub sync`,
  초대 전용)나 JEV 판정기를 설정하면 해당 기능은 네트워크와 별도 자격 증명을 쓰며
  설치에 포함되지 않습니다.

## 설치

공개 저장소는 인증 없이 clone할 수 있습니다.

```sh
PW="$HOME/tools/portwright"           # 아무 디렉터리; 공백이 있으면 항상 따옴표로 감쌉니다
git clone https://github.com/foxion37/portwright.git "$PW"
```

업스트림을 따라가는 방식을 고릅니다.

| 선택 | 명령 | 이후 업그레이드 |
|---|---|---|
| `main` 추적(clone 직후 기본) | 추가 없음 | `"$PW/bin/portwright" update` — `origin/main`을 가져와, 내 수정과 충돌하는 파일이 있으면 거부하고, 아니면 보호된 `git pull --ff-only` 후 오래된 노트를 재검증하고 설정된 문서를 갱신합니다. 새 노트도 함께 들어옵니다. |
| 릴리스 고정 | `git -C "$PW" checkout v4.0.2` | 수동: `git -C "$PW" fetch --tags && git -C "$PW" checkout <새-태그>`. detached 태그 체크아웃에서는 `update`를 쓰지 마세요. 항상 `origin/main`을 대상으로 하며 그 경우는 검증되지 않았습니다. |

릴리스는 `v4.0.2` 같은 태그입니다. 내가 만든 노트(`services/_drafts/`,
`failures/_drafts/`, 승격된 노트)는 **클론 안에** 써지므로 `main` 추적 update와 충돌할
수 있습니다. 먼저 `update --dry-run --json`으로 미리 확인하세요.

### 선택: 격리된 가상환경

필수가 아닙니다. 만들더라도 *내 셸*이 보는 `python3`만 바뀝니다.

```sh
python3 -m venv "$HOME/.venvs/portwright"
. "$HOME/.venvs/portwright/bin/activate"
"$PW/bin/portwright" check
```

주의: `bin/portwright`는 호출한 쪽의 `PATH`에 있는 `python3`를 실행합니다. AI Client는
관리 블록의 명령을 **자기 프로세스와 셸**에서 실행하므로 내가 활성화한 venv가 보이지
않습니다. Client의 `python3`가 3.9 이상이어야 하며 venv는 Client에게 아무 도움이
되지 않습니다. Claude Code 훅은 `bash`만 실행하고 Python을 쓰지 않습니다.

## 확인과 첫 사용

```sh
"$PW/bin/portwright" check                 # 노트별 [PASS]/[FAIL]과 "N passed, 0 failed"; 종료 코드 0
"$PW/bin/portwright" preflight github      # 캐시 HIT: 공개 배포된 Procedure
"$PW/bin/portwright" preflight some-tool-nobody-documented   # 캐시 MISS
```

- **HIT**: 첫 줄이 `READY: github`이고 이어서 `Procedure: services/github.md`,
  `Freshness:`, `Tier:`, 활성 Lesson이 나옵니다. `Profile:` 줄은 내 `profiles/`에
  따라 달라지며(배포본에는 없음) 특정 값을 기대하면 안 됩니다. 현재 공식 문서에서
  읽은 `--evidence-version`을 넘기기 전까지 `Freshness: unknown`은 정상입니다.
- **MISS**: `DERIVE REQUIRED: <service>`와 `Procedure: (cache miss)`, 그리고 정확한
  `memory draft procedure <service>` 명령이 든 `Next:` 줄이 나옵니다. *배포된*
  Procedure가 없는 서비스(작성자 개인용 노트인 서비스 포함)는 당연히 MISS이며,
  "직접 도출하라"는 뜻이지 설치가 깨졌다는 뜻이 아닙니다. `--json`으로 기계 판독
  형태를 얻을 수 있습니다.
- tier는 권고입니다. `confirm`과 `forbid`는 Client 자체의 권한 프롬프트가 지켜야
  합니다.

기여자용 Python `unittest` 스위트가 `tests/`에 있습니다. 설치 게이트가 아니므로 그
결과(통과/실패)를 설치 성공의 증거로 삼지 말고 위의 `check`와 `preflight`를 쓰세요.

## 명령

```sh
"$PW/bin/portwright" preflight <service> [--json] [--intent call|instruct|recover]
"$PW/bin/portwright" memory draft procedure <service>   # 또는: memory draft lesson <service> <slug>
"$PW/bin/portwright" memory review <draft-path>
"$PW/bin/portwright" memory promote <draft-path>
"$PW/bin/portwright" update --dry-run --json   # 미리보기
"$PW/bin/portwright" update                    # 적용 (main 추적 전용)
"$PW/bin/portwright" client --help             # Client 어댑터 설치/제거/점검
"$PW/bin/portwright" browser select --candidates candidates.json --goal "<text>"
```

`--home DIR`를 받는 명령은 `PORTWRIGHT_HOME`도 받으며 둘 다 콘텐츠 홈을 정합니다.
없으면 콘텐츠는 클론 안에 있습니다. `PORTWRIGHT_HOME`을 클론이 아닌 다른 디렉터리로
export했다면 `update`는 여전히 클론을 대상으로 해야 하므로 명시하세요:
`"$PW/bin/portwright" update --home "$PW"`(git이 아닌 별도 콘텐츠 홈에는 `VERSION`이
없어 `update`가 오류를 냅니다). `PORTWRIGHT_HOME`이 없으면 그냥 `update`가 맞습니다.

## 어댑터 안내 설치 vs. AI Client / MCP 등록

`client install`은 **지침 텍스트만** 설치합니다(Claude Code와 Codex는 스킬 심볼릭
링크 2개, Claude는 `SessionStart` 훅 추가). 에이전트가 도구를 쓰기 전에 `preflight`를
실행하도록 알려 주는 것이 전부입니다. 어떤 Client에도 Portwright를 MCP 서버로
**등록하지 않으며** AI Client를 실행하지도 않습니다.

선택 사항인 stdio MCP 서버 두 가지는 Client 자체의 MCP 설정에 직접 등록합니다.

```text
command: <$PW의 절대 경로>/bin/portwright
args:    mcp --home <$PW의 절대 경로>              # 도구: preflight, get_note, status
args:    skills serve --home <$PW의 절대 경로>     # 서버 이름: portwright-skills
```

셸 확장 없는 절대 경로를 쓰고, Client 설정이 한 문자열이면 공백을 따옴표로
감쌉니다. Client별 설정 파일 문법은 각 Client 몫이며 여기서 검증하지 않았습니다.

## Client별 설치

실제로 쓰는 Client만 고르세요. 각각 독립입니다. 설치는 실제 홈(예: `~/.codex/AGENTS.md`)에
쓰므로, 먼저 임시 홈으로 시험할 수 있습니다(모든 `client` 명령이 `--user-home` 지원).

```sh
"$PW/bin/portwright" client install codex --user-home "$(mktemp -d)"
```

| Client | 명령 | 건드리는 곳(`~` 기준) |
|---|---|---|
| Claude Code | `"$PW/bin/portwright" client install claude-code` | `.claude/settings.json`(SessionStart 훅), `.claude/skills/portwright-*` 심볼릭 링크 |
| Codex | `"$PW/bin/portwright" client install codex` | `.codex/AGENTS.md` 블록, `.codex/skills/portwright-*` 심볼릭 링크 |
| Hermes | `"$PW/bin/portwright" client install hermes` | `.hermes/SOUL.md` 블록 |
| Gemini CLI | `"$PW/bin/portwright" client install gemini-cli` | `.gemini/GEMINI.md` 블록 |
| OpenCode | `"$PW/bin/portwright" client install opencode` | `.config/opencode/AGENTS.md` 블록 |
| Oh My Pi | `"$PW/bin/portwright" client install oh-my-pi` | `.omp/agent/AGENTS.md` 블록 |
| VS Code Copilot | `"$PW/bin/portwright" client install vscode` | `.copilot/instructions/portwright.instructions.md` |
| Cursor | `"$PW/bin/portwright" client install cursor` | 없음 — 붙여 넣을 블록을 출력(수동) |

**Cursor는 설계상 수동입니다.** Cursor의 전역 User Rules는 앱 내부에 있고 CLI가 안전하게
고칠 수 있는 파일이 아닙니다. 명령은 내 클론의 절대 경로가 들어간 블록을 출력하고
`[ACTION-REQUIRED]`로 종료 코드 0을 냅니다. Cursor Settings → Rules → User Rules를 열어
한 번 붙여 넣으세요. `doctor cursor`는 항상 `[MANUAL]`만 보고할 수 있습니다.

### 선택한 Client 하나 확인

```sh
"$PW/bin/portwright" client doctor codex
```

성공 시 종료 코드 0과 `[OK] codex: portwright-tool-use=ok, portwright-tool-memory=ok,
managed-blocks=1` 같은 줄이 나옵니다. `[PARTIAL]`, `[NOT-INSTALLED]`는 종료 코드 1이며
빠진 항목을 알려 줍니다.

인자 없는 `portwright doctor`나 `client doctor`가 전부 초록일 것이라고 기대하지 마세요.
여덟 Client **전부**를 점검하므로 모두 쓰지 않는 컴퓨터에서는 `[NOT-INSTALLED]`와 함께
종료 코드 1이 나옵니다. 정상이며 충돌이 아닙니다. 설치한 Client 하나만 확인하세요.

### 설치/제거 동작

- **멱등**: 재설치하면 관리 블록 하나를 제자리에서 교체합니다. 중복되지 않고 내용이
  같으면 파일을 다시 쓰지 않습니다.
- **보존**: `<!-- portwright:start … -->` / `<!-- portwright:end -->` 밖의 텍스트는
  유지됩니다. 변경 전 같은 위치에 `<이름>.bak-<타임스탬프>` 백업을 만듭니다.
- **덮어쓰지 않고 거부**: 깨진 `settings.json`, 심볼릭 링크 자리에 있는 Portwright가
  아닌 기존 경로, `applyTo` frontmatter 없는 `portwright.instructions.md`, 심볼릭 링크
  대상은 stderr에 `portwright: …`를 내고 종료 코드 2로 멈추며 아무것도 바꾸지
  않습니다.
- **클론 경로 기록**: 블록과 심볼릭 링크가 `$PW`의 절대 경로를 가리킵니다. 클론을
  옮기거나 이름을 바꾸면 `client install`을 다시 실행하세요.
- **제거**: `"$PW/bin/portwright" client remove <client>`는 관리 블록, 정확히 일치하는
  훅 명령, 이 클론을 가리키는 심볼릭 링크만 지웁니다. 클론, 내 노트, 백업은 지우지
  않습니다.

각 어댑터 문서([`adapters/`](adapters/))에 정확한 접점과 복구 방법이 있습니다.

## 완전 제거

먼저 백업하세요. 클론에는 내가 쓴 노트(`services/`, `failures/`, 초안)와 로컬 설정이
있고 `rm -rf`는 이를 지웁니다. 어댑터 제거 후에도 수정된 Client 파일 옆에
`.bak-<타임스탬프>` 파일이 남습니다.

클론을 삭제하기 전에 Cursor User Rules에 직접 붙인 Portwright 블록과
Client에 수동으로 등록한 Portwright MCP 항목도 제거하세요.
`client remove`는 이 수동 등록을 지우지 못합니다.

```sh
cp -R "$PW" "$PW.backup"        # 또는 내 노트/설정만 다른 곳에 복사
"$PW/bin/portwright" client remove codex   # 설치한 Client마다 반복
rm -rf "$PW"                    # 어댑터를 먼저 제거한 뒤에만
```
