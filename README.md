# Portwright

![Portwright cover: a tool-use memory path for LLM agents](./assets/portwright-cover.png)

> A tool-use memory layer for LLM agents. Portwright helps an agent remember how external tools were connected, what failed before, and which steps still require a human.

Korean companion: [README_KR.md](README_KR.md)

## In Plain Words

**Portwright makes an agent read a short note about a tool before it touches that tool.**

It does not run the tool for you. Your agent still calls the CLI, the MCP server, or the API. Portwright is the step *before* that: it hands the agent what was already learned about this tool, on this machine, for this project.

Three kinds of notes do the work:

| Note | Lives in | Holds |
|---|---|---|
| **Procedure** | `services/<tool>.md` | The steps that are correct today, what only a human can do (`human_steps`), what the agent can do alone (`agent_can`), and what it must never ask you for |
| **Lesson** | `failures/<date>-<tool>-<symptom>.md` | What was tried, the confirmed root cause, and the corrected move |
| **Profile** | `profiles/<project>.md` | Which GitHub account, secret source, and database this directory belongs to |

One call answers four questions at once:

```text
$ portwright preflight google-apps-script
READY: google-apps-script
Profile: portwright (github <account>, env 1password/DEVELOPER, db -)
Procedure: services/google-apps-script.md
Freshness: fresh (version match only; procedure steps not revalidated)
Tier: confirm
Active Lessons: 0
```

Which account am I on, what is the procedure, can I trust this note, and should I ask the user first.

Three promises keep it honest:

1. **Freshness never flatters.** `fresh` requires evidence. Without evidence the answer is `unknown` plus "verify first" — `unknown` is never promoted to `fresh`. Even `fresh` claims only that the versions agree, not that every step was re-proved.
2. **The tier is advice, not a gate.** Portwright reports `auto|confirm|forbid` and stops there; your Client's own permission prompt enforces it. Unmatched or unknown falls back to `confirm` — when in doubt, ask.
3. **The cache fills itself.** There is no pre-written library of 500 tools. The first time a tool is used with no note, the agent derives the current procedure from official docs and caches it. That is the whole product: knowledge that arrives by being needed.

## Why It Exists

AI agents often fail around the same external-tool chores:

- They ask the user to create or paste a token even when a connector, CLI, or MCP tool could do the work.
- They repeat an old dashboard path after the service UI has moved.
- They hit the same SDK, API, or permission error because the previous fix was never written down.

Portwright keeps those lessons in a small, readable repo. Before the agent acts, it checks the notes. If the notes are missing or stale, the agent refreshes them from current docs instead of guessing.

## What Portwright Does

Portwright has three jobs, plus a router since 2.0.0:

1. **Connect once, remember it.** Use whichever backend can actually execute the task: Composio, native MCP, a CLI, or a direct API.
2. **Verify before instructing.** If a step truly needs the user, the agent checks the current path before giving instructions.
3. **Reuse hard-won lessons.** When a tool call fails and gets fixed, the fix is saved so the next run can avoid the same failure.

Since **2.0.0**, Portwright is also a **profile router**: a `profiles/<id>.md` file binds each project directory to its GitHub account, env source, and databases, so `preflight` answers for the right account. It also reports a deterministic freshness state (`fresh|stale|unknown` — a version match proves the versions agree, not that the whole procedure is still correct) and an advisory approval tier (`auto|confirm|forbid`), and `portwright update` distributes new notes through its own guarded `git pull --ff-only` while re-checking stale ones against their local contracts. Still no gateway — the tier is advice the Client's own permission prompt enforces.

## What It Is Not

Portwright v1 is deliberately small.

- It is not a gateway.
- It is not a policy engine.
- It is not a background server.
- It is not an audit pipeline.
- It is not a pre-filled registry of every tool on the internet.

The repo stores procedures and lessons. Your agent reads them before acting.

## Who It Is For

This README is written for beginners and non-developers.

You do not need to code to understand the workflow. If you can approve an occasional OAuth screen and follow a short command, that is enough. The agent should do the rest when it can.

## How It Works

```text
You ask an agent to use a tool
        |
        | resolves the Profile for this directory (2.0.0)
        v
Profile: account + env source + databases
        |
        | reads services/ and failures/ first
        v
Portwright procedure + lesson cache
        |
        | chooses the backend that can execute
        v
Composio / native MCP / CLI / direct API
        |
        v
Vercel, Notion, Supabase, GitHub, ...

`portwright update` keeps the cache current (it runs the guarded `git pull --ff-only` itself — never pull first)
```

The folders that matter most:

- `profiles/*.md` binds a project directory to its GitHub account, env source, databases, and default tier (2.0.0).
- `services/*.md` stores the current procedure for a service: what the agent can do, and what only the human can do.
- `failures/*.md` stores lessons from real failures: the root cause and the next correct move.
- `services/_private/` and `failures/_private/` hold `distributable: false` notes — personal, gitignored, read by the same catalog (2.0.0).
- `services/_private/_evidence/` caches official docs that `update` downloaded for Procedures declaring `freshness_evidence.url` — apply only, dry-run never downloads (2.0.0).

## Example

Instead of:

> "Go to the dashboard, make an API key, paste it here, then I will try again."

The agent should first ask:

1. Is there already a working connection or CLI login?
2. Is there a cached procedure in `services/`?
3. Did a previous run already record this failure in `failures/`?
4. If the user must click something, what do the current official docs say?

Only the unavoidable human step should reach the user.

## What Ships

- `install/`: setup notes, adapters, schemas, and reusable snippets
- `bin/portwright`: one interface for preflight, contract checks, memory, and Client adapters
- `skills/portwright-tool-use/`: the read-before-acting skill
- `skills/portwright-tool-memory/`: the write-carefully memory skill
- `services/`, `failures/`, and `profiles/`: cache folders that grow through use, plus `_private/` roots for personal notes

## Install

Start with [install/README.md](install/README.md).

Short version:

1. Clone this repo somewhere stable on your machine.
2. Pick the Client adapter:
   - [Claude Code](install/adapters/claude-code.md)
   - [Codex](install/adapters/codex.md)
   - [Hermes](install/adapters/hermes.md)
   - [Gemini CLI](install/adapters/gemini-cli.md)
   - [Cursor](install/adapters/cursor.md)
   - [OpenCode](install/adapters/opencode.md)
   - [Oh My Pi](install/adapters/oh-my-pi.md)
   - [VS Code Copilot](install/adapters/vscode.md)
3. Run the matching install command, for example:

```sh
bin/portwright client install codex
```

4. Restart the Client, then verify both memory and installation:

```sh
bin/portwright check
bin/portwright client doctor codex
```

## Use

Ask your agent to use an external tool as usual. Portwright changes what the agent does before it answers or calls the tool:

1. Run `bin/portwright preflight <service-id>` — it resolves the Profile for the current directory, then returns the Procedure, active Lessons, a freshness state, and an advisory tier.
2. Read the returned Procedure and active Lessons.
3. Act through the already-connected backend when possible.
4. Ask the user only for a genuinely human-only step, such as approving OAuth.
5. If something new breaks and the root cause is confirmed, create a Lesson
   draft and promote it only after validation.

```sh
bin/portwright memory draft lesson <service-id> <slug>
bin/portwright memory promote <draft-path>
```

2.0.0 commands:

```sh
bin/portwright preflight <service-id> [--intent call|instruct|recover] [--profile <id>] [--json]
bin/portwright browser select --candidates candidates.json --goal "<text>" [--json]   # file holds the candidate list
bin/portwright update --dry-run --json   # preview: update runs git pull --ff-only itself, never pull first
bin/portwright update
```

Example prompts:

- "Use the cached Vercel notes before deploying this preview."
- "Before telling me how to open Notion OAuth, check the current path."
- "Use the Supabase lesson from last time so we do not repeat the same error."

## References

Portwright's v1 shape is based on the local design docs in this repo:

- [ARCHITECTURE.md](ARCHITECTURE.md): the six purposes, why there is no gateway, and the 2.0.0 profile router
- [HANDOFF.md](HANDOFF.md): the approved v1 and 2.0.0 build orders
- [CONTEXT.md](CONTEXT.md): glossary for terms such as Procedure, Lesson, and Verify-before-instruct
- [FUTURE.md](FUTURE.md): deferred gateway, policy, and audit ideas

External references:

- [Model Context Protocol](https://modelcontextprotocol.io/) for the general tool-connection pattern used by MCP clients and servers
- [OpenAI Terms of Use](https://openai.com/policies/row-terms-of-use/) for ownership and use of ChatGPT output

## Credits And Prior Art

Portwright is small because other people already solved parts of it. What was borrowed, and from where:

| Repository | What Portwright took |
|---|---|
| [changeroa/gisul](https://github.com/changeroa/gisul) | The remote serving shape: Git main → CI validation → immutable commit-pinned release → digest-verified reads → promote/rollback pointer. Portwright reuses it as the backend for serving public notes instead of building its own. |
| [tsouth89/toolport](https://github.com/tsouth89/toolport) | Keeping the always-visible tool surface small and returning references first, and the habit of measuring mechanics (schema tokens, latency) separately from model-graded quality. |
| [upstash/context7](https://github.com/upstash/context7) | Putting the tool version in the cache key: if the version differs, re-derive rather than trust. |
| [os-tack/docfresh](https://github.com/os-tack/docfresh) | Recording the upstream reference point at verification time and calling a note stale when the current reference point differs — the deterministic half of the freshness table. |
| [chopratejas/invalidate](https://github.com/chopratejas/invalidate) | The TypeSafe Jev pattern: cheap yes/no judgments over stored facts, and the rule that a question or an instruction is not evidence. |
| [mem0ai/mem0](https://github.com/mem0ai/mem0) | Supersede-on-contradiction and down-ranking instead of deleting, behind the `status: stale` model for Lessons. |
| [microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp), [browser-use/browser-use](https://github.com/browser-use/browser-use) | Serializing page elements as a numbered/`[ref=eN]` candidate list so a decision model can pick one by reference. |
| [browserbase/stagehand](https://github.com/browserbase/stagehand), [OSU-NLP-Group/Mind2Web](https://github.com/OSU-NLP-Group/Mind2Web) | Separating "collect candidates" from "choose one", and letting a cheap model narrow before an expensive one decides. |
| [calghar/gh-account-switcher](https://github.com/calghar/gh-account-switcher), [yinklylab/git-switch](https://github.com/yinklylab/git-switch) | A profile as a bundle (account + identity + keys) and a `doctor`-style self-check. |
| [jdx/mise](https://github.com/jdx/mise), [Shopify/shadowenv](https://github.com/Shopify/shadowenv) | Directory-based resolution with named overlays, and the idea that a directory must be trusted before its config is applied automatically. |
| [1mcp-app/agent](https://github.com/1mcp-app/agent) | A dotfile at the project root deciding that project's agent context. |
| [vercel-labs/skills](https://github.com/vercel-labs/skills), [numman-ali/openskills](https://github.com/numman-ali/openskills) | Recording install source and a content hash, and refusing to update when provenance is unclear. Their "wipe and re-copy" update is deliberately *not* copied, because it would destroy local notes. |
| [Composio](https://composio.dev/), [Model Context Protocol](https://modelcontextprotocol.io/) | Connection backends. Portwright is backend-agnostic: whichever one can actually execute wins. |
| Lunar MCPX, Lasso, ContextForge | Evaluated as gateway engines and deliberately not adopted. Their allow/block model has no human-in-the-loop step, which is why Portwright's tier stays advisory. See [FUTURE.md](FUTURE.md) and `docs/adr/0003`. |

No single existing project covers Portwright's combination: connection *and* usage procedures bound to a profile, a tool-use failure cache with confirmed root causes, private notes that never distribute, and client-agnostic delivery.

## License And Image Credit

MIT License — see [LICENSE](LICENSE). Copyright (c) 2026 foxion37. MIT covers both the code and the notes in `services/` and `failures/`; the cover image below follows its own credit line instead.

Cover image: ChatGPT-generated image provided by the repo owner on 2026-06-23. The image is used here as the Portwright GitHub cover. Under OpenAI's Terms of Use, OpenAI assigns its right, title, and interest in Output to the user to the extent permitted by law; users are still responsible for checking that their use is lawful and appropriate.
