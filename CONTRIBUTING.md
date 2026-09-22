# Contributing

한국어 안내는 아래에 함께 있습니다.

**This tree is generated.** It is a one-way export from a private workstation repository that also holds personal notes which must never be published. Commits made directly here are overwritten by the next export.

So contributions work like this:

- **Issues are the best path.** A wrong procedure, a stale command, a missing lesson, a bug — open an issue.
- **Pull requests are welcome and read, but not merged as commits.** Accepted content is ported into the source repository and arrives here in the next export. Your content lands; your commit does not. Say in the PR how you want to be credited.
- **Never send** credentials, personal notes, profiles, machine paths, account handles, or organization names. The export gate rejects them anyway.

Before proposing a change, read [AGENTS.md](AGENTS.md) — the note contract, the draft-first memory lifecycle, and the semantics that must not be softened.

Verify with:

```sh
python3 -m unittest discover -s tests -q
bin/portwright check
```

Licensed under MIT; see [LICENSE](LICENSE). By contributing you agree your contribution ships under the same license.

---

**이 트리는 생성물입니다.** 절대 공개되면 안 되는 개인 메모가 함께 있는 비공개 작업 저장소에서 단방향으로 내보낸 결과입니다. 여기에 직접 한 커밋은 다음 내보내기 때 덮입니다.

- **이슈가 가장 좋은 경로입니다.** 틀린 절차, 낡은 명령, 빠진 교훈, 버그는 이슈로 알려 주세요.
- **Pull Request도 환영하고 읽지만 커밋으로 머지되지는 않습니다.** 채택된 내용은 원본 저장소로 옮겨 적고 다음 내보내기로 여기 반영됩니다. 내용은 남고 커밋은 남지 않습니다. 어떻게 표기되길 원하는지 PR에 적어 주세요.
- **보내면 안 되는 것**: 자격증명, 개인 메모, 프로파일, 특정 컴퓨터 경로, 계정 핸들, 조직명. 어차피 내보내기 게이트가 거부합니다.

변경을 제안하기 전에 [AGENTS_KR.md](AGENTS_KR.md)를 읽으세요. 메모 계약, 초안 우선 흐름, 약하게 바꾸면 안 되는 의미들이 적혀 있습니다.

라이선스는 MIT입니다([LICENSE](LICENSE)). 기여하면 같은 라이선스로 배포되는 데 동의하는 것으로 봅니다.
