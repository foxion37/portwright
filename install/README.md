# portwright — install

portwright is a model-agnostic **tool-use knowledge layer** for AI agents.
It does two things: **(A)** minimize the user's setup intervention, and
**(B)** minimize the agent's own tool-use failures/tokens — so the agent
succeeds first-try by reading a cache of verified procedures + past failures
*before* it acts.

## Ships vs. user-local

This package ships only the **spec + two skills + templates + schema + CLI +
adapters**. The actual knowledge is **user-local** and **self-fills**: the agent
writes a `services/<tool>.md` note the first time it resolves a tool (derive-on-
miss), and a `failures/` note the first time it hits and fixes a new failure. So
the repo you clone is the *machinery*; the notes grow on your machine.

| Shipped (this package) | User-local (self-filling) |
|---|---|
| `install/snippets/portwright.block.md` — canonical rule template rendered with the install root | `services/*.md` — verified procedures |
| `install/schema/*.schema.json` — frontmatter contracts | `failures/*.md` — lessons |
| `install/adapters/*.md` — per-agent install steps | |
| `install/portwright-reminder.sh` — Claude hook reminder | |
| `bin/portwright` — preflight / contract / memory / Client CLI | |
| `skills/portwright-tool-use` — read before acting | |
| `skills/portwright-tool-memory` — draft safe lessons | |

## This is NOT

…an app or a server. portwright is **markdown notes + two small skills + a hook**. The
connection backends (Composio / native MCP / CLI) are swappable plumbing, not
the identity.

## Use

```sh
bin/portwright preflight vercel    # resolve Profile, Procedure, Lessons, freshness, tier
bin/portwright browser select --candidates candidates.json --goal "<text>"  # pick one candidate via JEV (file holds the list)
bin/portwright update --dry-run --json   # preview; update performs its own guarded git pull --ff-only, do not pull first
bin/portwright update                   # apply: guarded fast-forward pull + stale re-validation + reports
bin/portwright check              # validate Procedure, Lesson, and draft contracts
bin/portwright memory --help      # create, review, and promote safe drafts
bin/portwright client --help      # install, remove, and inspect Client adapters
bin/portwright doctor             # compatibility health report
```

Since 2.0.0, `preflight` also returns the resolved `profile`, a `freshness`
state (`fresh|stale|unknown`), and an advisory `tier` (`auto|confirm|forbid`)
with `rationale`. The tier is advice, not enforcement: `confirm` and `forbid`
must be honoured by the Client's own permission prompt.

Both commands accept `--home DIR` (or the `PORTWRIGHT_HOME` env var); the default
home is `$HOME/developer/tools/portwright`.

## Per-agent install

- Claude Code → `bin/portwright client install claude-code`
- Codex → `bin/portwright client install codex`
- Hermes → `bin/portwright client install hermes`
- Gemini CLI → `bin/portwright client install gemini-cli`
- Cursor → `bin/portwright client install cursor` and follow the one manual User Rules step
- OpenCode → `bin/portwright client install opencode`
- Oh My Pi → `bin/portwright client install oh-my-pi`
- VS Code Copilot → `bin/portwright client install vscode`

Each adapter document under [`adapters/`](adapters/) explains its exact seam and
recovery path. File-backed installs are idempotent, create a backup before a
change, and preserve content outside the managed block.

## Clean-clone install check

To confirm a fresh clone works before wiring any agent, run the CLI against the
clone itself:

```sh
git clone <repo-url> portwright-check
cd portwright-check
bin/portwright check --home "$PWD"
bin/portwright preflight vercel --home "$PWD"
python3 -m unittest discover -s tests -v
```

Expected results:

- `check` passes: every tracked Procedure and Lesson validates against its full
  contract, including nested fields, dates, filenames, and root cause sections.
- `preflight` returns the cached Vercel Procedure and active Lessons.
- `client doctor` may report an adapter as not installed on a clean machine.
