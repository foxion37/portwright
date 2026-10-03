# portwright — install

portwright is a model-agnostic **tool-use knowledge layer** for AI agents.
It does two things: **(A)** minimize the user's setup intervention, and
**(B)** minimize the agent's own tool-use failures/tokens — so the agent
succeeds first-try by reading a cache of verified procedures + past failures
*before* it acts.

## Ships vs. user-local

This package ships the **spec + two skills + templates + schema + CLI +
adapters**, plus a set of public example Procedures (`services/`, e.g.
`github`) and Lessons (`failures/`). Your own knowledge is **user-local** and
**self-fills**: the agent writes a `services/<tool>.md` note the first time it
resolves a tool that has none (derive-on-miss), and a `failures/` note the first
time it hits and fixes a new failure. So the repo you clone is the *machinery*
plus a starter set; the notes grow on your machine.

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

## Requirements

- `git` (clone, and `portwright update`) and `bash` (the `bin/portwright` launcher).
- `python3`, **3.9 or newer supported**, on the `PATH` of the process that runs
  it. Tested scope: upstream CI runs 3.12, and an isolated-venv smoke run
  (clone, `check`, `preflight`) was done on 3.14.6. Other versions, including
  3.9 to 3.11, are the supported floor but not systematically tested; older than
  3.9 is unsupported.
- **Local CLI: no `pip install`, no package, no third-party dependency.** The
  commands in this guide use the Python standard library only. Separately,
  operator tooling for publishing and for the Hub (`scripts/`, `hub/`) is not
  part of installation and can need Node.js or a YAML module. A virtualenv is
  optional (see below).
- macOS or Linux with a POSIX shell. Windows is not exercised.
- The baseline local workflow (`check`, `preflight`, `memory`, `client`) needs no
  credentials and no network. `update` needs `git` access to `origin`. If you
  configure a Hub (`hub sync`, invite-only) or a JEV judge, those features
  use the network and their own credentials; neither is part of installation.

## Install

The public repository is cloneable without authentication.

```sh
PW="$HOME/tools/portwright"           # any directory; spaces are fine if quoted everywhere
git clone https://github.com/foxion37/portwright.git "$PW"
```

Choose how the clone follows upstream (this is the only upgrade decision):

| Choice | Command | Later upgrade |
|---|---|---|
| Tracking `main` (default after clone) | nothing more | `"$PW/bin/portwright" update` — fetches `origin/main`, refuses if your local edits collide with incoming files, otherwise a guarded `git pull --ff-only`, then re-validates stale notes and refreshes configured docs. New notes arrive with it. |
| Pinned release | `git -C "$PW" checkout v4.0.2` | Manual: `git -C "$PW" fetch --tags && git -C "$PW" checkout <newer-tag>`. Do not use `update` on a detached tag checkout — it always targets `origin/main`, and that case is not verified. |

Releases are tags like `v4.0.2`. Your own notes (`services/_drafts/`,
`failures/_drafts/`, promoted notes) are written **into the clone**, so a
tracking-`main` update can collide with your edits; `update --dry-run --json`
previews that first.

### Optional: isolated virtualenv

Not required. If you want one anyway, it only changes which `python3` your
*shell* sees:

```sh
python3 -m venv "$HOME/.venvs/portwright"
. "$HOME/.venvs/portwright/bin/activate"
"$PW/bin/portwright" check
```

Caveat: `bin/portwright` runs `python3` from the `PATH` of whoever calls it. An
AI Client launches the command from the managed block in **its own** process
and shell, where your activated venv is not present. The Client's `python3`
must therefore itself be 3.9+; the venv adds nothing for the Client. The
Claude Code hook runs only `bash`, no Python.

## Check and first use

```sh
"$PW/bin/portwright" check                 # prints [PASS]/[FAIL] per note and "N passed, 0 failed"; exit 0
"$PW/bin/portwright" preflight github      # cache HIT: a shipped public Procedure
"$PW/bin/portwright" preflight some-tool-nobody-documented   # cache MISS
```

- **Hit**: the first line is `READY: github`, followed by `Procedure:
  services/github.md`, `Freshness:`, `Tier:`, and the active Lessons. The
  `Profile:` line depends on your own `profiles/` (none ship); do not expect
  anything particular there. `Freshness: unknown` is normal until you pass
  `--evidence-version` from current official docs.
- **Miss**: `DERIVE REQUIRED: <service>` with `Procedure: (cache miss)` and a
  `Next:` line giving the exact `memory draft procedure <service>` command. A
  miss is expected for any service without a *distributed* Procedure, including
  services whose note is private to its author — it means "derive it", not that
  the install is broken. Rerun with `--json` for a machine-readable form.
- The tier is advisory: `confirm` and `forbid` must be honoured by the Client's
  own permission prompt.

There is also a Python `unittest` suite under `tests/` for contributors. It is
not an install gate: do not treat a green or red run of it as proof that the
install works; use `check` and `preflight` above.

## Commands

```sh
"$PW/bin/portwright" preflight <service> [--json] [--intent call|instruct|recover]
"$PW/bin/portwright" memory draft procedure <service>   # or: memory draft lesson <service> <slug>
"$PW/bin/portwright" memory review <draft-path>
"$PW/bin/portwright" memory promote <draft-path>
"$PW/bin/portwright" update --dry-run --json   # preview
"$PW/bin/portwright" update                    # apply (tracking main only)
"$PW/bin/portwright" client --help             # install, remove, inspect Client adapters
"$PW/bin/portwright" browser select --candidates candidates.json --goal "<text>"
```

Commands that accept `--home DIR` also accept `PORTWRIGHT_HOME`; both select the
content home. Without either, content lives in the clone itself. Packaged
schemas, snippets, and skills come from the clone when absent from that home.
If you export `PORTWRIGHT_HOME` to a directory other than the clone, `update`
still needs the clone as its target — pass it explicitly:
`"$PW/bin/portwright" update --home "$PW"` (a separate non-git content home has no
`VERSION` and `update` errors). With `PORTWRIGHT_HOME` unset, plain `update` is
correct.

## Adapter guidance vs. AI Client / MCP registration

`client install` **only installs instruction text** (and, for Claude Code and
Codex, two skill symlinks plus the Claude `SessionStart` hook) so that the
agent is told to run `preflight` before using a tool. It does **not** register
Portwright as an MCP server in any Client and does not launch any AI Client.

Two optional stdio MCP servers exist and you register them yourself, using the
Client's own MCP settings, as a command with arguments:

```text
command: <absolute path of $PW>/bin/portwright
args:    mcp --home <absolute path of $PW>        # tools: preflight, get_note, status
args:    skills serve --home <absolute path of $PW>  # server name: portwright-skills
```

Use absolute paths without shell expansion; where the Client's config is a
single string, quote spaces. Each Client's config file syntax is its own and
is not documented or verified here.

## Per-agent install

Pick the Client(s) you really use; each is independent. Installing writes to
your real home (for example `~/.codex/AGENTS.md`), so preview with a throwaway
home first if you like (every `client` command accepts `--user-home`):

```sh
"$PW/bin/portwright" client install codex --user-home "$(mktemp -d)"
```

| Client | Command | Touches (under `~`) |
|---|---|---|
| Claude Code | `"$PW/bin/portwright" client install claude-code` | `.claude/settings.json` (SessionStart hook), `.claude/skills/portwright-*` symlinks |
| Codex | `"$PW/bin/portwright" client install codex` | `.codex/AGENTS.md` block, `.codex/skills/portwright-*` symlinks |
| Hermes | `"$PW/bin/portwright" client install hermes` | `.hermes/SOUL.md` block |
| Gemini CLI | `"$PW/bin/portwright" client install gemini-cli` | `.gemini/GEMINI.md` block |
| OpenCode | `"$PW/bin/portwright" client install opencode` | `.config/opencode/AGENTS.md` block |
| Oh My Pi | `"$PW/bin/portwright" client install oh-my-pi` | `.omp/agent/AGENTS.md` block |
| VS Code Copilot | `"$PW/bin/portwright" client install vscode` | `.copilot/instructions/portwright.instructions.md` |
| Cursor | `"$PW/bin/portwright" client install cursor` | nothing — prints a block for you to paste (manual) |

**Cursor is manual by design.** Cursor keeps global User Rules inside the app,
not in a file the CLI can safely edit. The command prints the block (with your
clone's absolute path) and exits 0 with `[ACTION-REQUIRED]`; open Cursor
Settings → Rules → User Rules and paste it once. `doctor cursor` can only ever
report `[MANUAL]`.

### Verify one Client

```sh
"$PW/bin/portwright" client doctor codex
```

Expected on success: exit 0 and a line like `[OK] codex:
portwright-tool-use=ok, portwright-tool-memory=ok, managed-blocks=1`.
`[PARTIAL]` or `[NOT-INSTALLED]` exit 1 and name what is missing.

Do not rely on bare `portwright doctor` or `client doctor` to be all green: they
check **every** adapter, so on any machine that does not use all eight Clients
they exit 1 with `[NOT-INSTALLED]` lines. That is expected, not a crash. Check
only the Client you installed.

### Behaviour of install and remove

- **Idempotent**: reinstalling replaces the one managed block in place; it is
  never duplicated, and an unchanged file is not rewritten.
- **Preserving**: text outside the `<!-- portwright:start … -->` /
  `<!-- portwright:end -->` markers is kept. Before any change the CLI copies the
  file to `<name>.bak-<timestamp>` next to it.
- **Refuses rather than overwrites**: malformed `settings.json`, an existing
  non-Portwright path where a skill symlink belongs, a `portwright.instructions.md`
  without the `applyTo` frontmatter, or a symlinked target all stop with
  `portwright: …` on stderr and exit 2, changing nothing.
- **Records the clone path**: the block and symlinks point to the absolute path
  of `$PW`. If you move or rename the clone, run `client install` again.
- **Remove**: `"$PW/bin/portwright" client remove <client>` deletes only the
  managed block, the exact hook command, and symlinks that point at this clone.
  It does not delete the clone, your notes, or the backups.

Each adapter document under [`adapters/`](adapters/) explains its exact seam and
recovery path. Korean readers: [`README_KR.md`](README_KR.md).

## Full uninstall

Back up first: the clone holds the notes you wrote (`services/`, `failures/`,
drafts) and any local config, and `rm -rf` deletes them. Adapter removal also
leaves `.bak-<timestamp>` files next to the edited Client files.

Before deleting the clone, also remove any Portwright block you pasted into
Cursor User Rules and any Portwright MCP entries you registered manually in
your Clients. `client remove` cannot remove those manual integrations.

```sh
cp -R "$PW" "$PW.backup"        # or copy only your own notes/config elsewhere
"$PW/bin/portwright" client remove codex   # repeat for each Client you installed
rm -rf "$PW"                    # only after removing adapters
```
