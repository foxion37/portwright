# Contributing

한국어 안내는 아래에 함께 있습니다.

**This tree is generated.** It is a one-way export from a private workstation repository that also holds personal notes which must never be published. Commits made directly here are overwritten by the next export.

Contributions of Procedures and Lessons are not accepted through this generated public repository before M2. The planned Hub MCP submission path will handle them once available; it is not live in v2.1.0. Do not send lesson text in GitHub Issue/PR free-form bodies. See [Roles and scope](docs/roles-and-scope.md) for the planned roles and intake boundary.

Code bugs in the CLI or Hub code can still be reported as issues; describe the behavior, not a lesson, and never paste logs or credentials.

Never send credentials, personal notes, profiles, machine paths, account handles, or organization names.

Before working on a local change, read [AGENTS.md](AGENTS.md) — the note contract, the draft-first memory lifecycle, and the semantics that must not be softened.

Verify with:

```sh
python3 -m unittest discover -s tests -q
bin/portwright check
```

Licensed under MIT; see [LICENSE](LICENSE). By contributing you agree your contribution ships under the same license.

---

**이 트리는 생성물입니다.** 절대 공개되면 안 되는 개인 메모가 함께 있는 비공개 작업 저장소에서 단방향으로 내보낸 결과입니다. 여기에 직접 한 커밋은 다음 내보내기 때 덮입니다.

M2 전에는 이 생성된 공개 저장소를 통해 절차나 교훈 기여를 받지 않습니다. 계획된 Hub MCP 제출 경로가 마련되면 그 경로로 받으며, v2.1.0에는 아직 제공되지 않습니다. GitHub Issue/PR의 자유 본문으로 교훈을 보내지 마세요. 계획된 역할과 접수 경계는 [역할과 범위](docs/roles-and-scope_KR.md)를 참고하세요.

CLI나 Hub 코드의 버그는 지금처럼 이슈로 알려 주세요. 교훈 본문이 아니라 동작을 적고, 로그나 자격증명은 붙이지 마세요.

자격증명, 개인 메모, 프로파일, 특정 컴퓨터 경로, 계정 핸들, 조직명은 보내지 마세요.

로컬 변경을 작업하기 전 [AGENTS_KR.md](AGENTS_KR.md)를 읽으세요. 메모 계약, 초안 우선 흐름, 약하게 바꾸면 안 되는 의미들이 적혀 있습니다.

라이선스는 MIT입니다([LICENSE](LICENSE)). 기여하면 같은 라이선스로 배포되는 데 동의하는 것으로 봅니다.
