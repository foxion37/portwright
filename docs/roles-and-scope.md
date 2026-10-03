# Roles and scope

Portwright helps an AI agent find current external-tool procedures and reusable lessons without asking you to repeat avoidable setup or failed calls. Its local CLI works on its own with a personal cache, and needs no account, network, or credential. The shared public Hub (M2) is deployed but invite-only, and its latest live SECRETS/INJECTION acceptance runs are unresolved, so it is optional and not proven. The company Hub (M3) is not approved and not available.

## Who does what

- **User:** A person using portwright, whether an outside developer, a company colleague, or a non-developer using an AI application. The user alone performs unavoidable human steps, such as consent.
- **Agent:** The AI inside a Client. It reads the relevant Procedure and Lessons, derives a missing or stale procedure from the tool's current official documentation, and performs the agent's share through an independently connected tool backend.
- **Client:** The application that hosts the Agent, such as an MCP-capable coding assistant. It installs or calls portwright; it retains its own permission controls. Portwright's action tier is advice, not enforcement.
- **Operator:** For the public Hub, issues invite tokens, reviews promotions to `stable`, and confirms recalls of `stable` revisions.
- **Company administrator:** Would issue company invite tokens and manage that audience's access. No company Hub is approved or available; it would be considered only after its deployment and repository rights are verified, and the Operator would confirm `stable` recalls there as well.

## Where code and knowledge live

Three repository roles are separate: a **private work repository** develops the CLI and Hub code and keeps the owner's personal content; a **public distribution repository** receives a one-way, screened export, currently including a starter set of distributable Procedures and Lessons; a **private commons repository** holds the canonical git copy of the shared Procedure/Lesson cache, grades, and recall markers for one Hub. Code-only export remains a separate migration step and has not been applied to the current public distribution. A company Hub would have its own private commons. Commons is a cache filled as tools are used, not a pre-filled registry: a missing or stale central procedure still follows derive-from-official-docs and cache-on-miss.

There are three audience-specific delivery surfaces. **Personal** delivery keeps the owner's existing private catalog, including personal skills, inaccessible to public or company tokens. The **public Hub** is a deployed, invite-only shared service with its own Worker, D1 intake, and release bucket; there is no self-signup and no address is published in this guide. The **company Hub** is not approved and not available; if later approved it would be a separately deployed service with its own credentials, storage, and commons. Each shared Hub uses one Worker, one D1, and one Actions workflow to carry content; neither Hub executes external tool calls. Public and company Hubs never fall back to each other. The personal catalog never enters either shared Hub.

## Reading and contributing

Locally, a note id resolves in this order: `_private` → tracked notes → the selected `_hub` snapshot. Duplicate ids within one source are errors; an id repeated across sources is a warned override. Hub sync writes a new local generation without changing code and switches both note roots through one state pointer. Only the selected Hub is read; public and company never fall back to one another. An unconfigured M1 home still reads its existing local `_hub` notes, but enabling a shared Hub hides those unverified notes and requires an active generation. Without a cached procedure, the Agent uses current official documentation and caches the derived result. Users do not need access to the private commons repository to read delivered notes.

Shared Hubs serve `stable` notes by default in preflight, bundles, and MCP reads. A `trial` note is experimental and appears locally only with explicit `--include-trial` or persistent `include_trial: true` on that Hub. Three confirmations from distinct invite-token lineages plus Operator review are required for `stable`; a changed revision needs fresh confirmations. Two distinct lineages can report a `trial` revision; `stable` recall also needs Operator confirmation. A recall targets the note id and revision digest, including pinned releases and local caches once synchronized. Offline clients apply all previously received recalls, but cannot learn new ones before reconnecting.

Submission uses MCP only (`submit_lesson`, `confirm_lesson`, `report_failure`); there is no CLI submit command or Issue/PR free-text intake. A configured local MCP server exposes those tools only when its selected Hub and token environment variable are available. It screens free text locally; confirmations and failure reports must also refer to a note id and revision in that Hub's active snapshot before any request is sent. A note read explicitly from company cannot be reported to a public default Hub. The Hub independently screens again before storage. Invite tokens are issued by the Operator for the public Hub or by a company administrator for the company Hub; there is no self-signup. They authenticate Hub access, not external-tool access. The Hub stores only their hash, organization, scope (`read|submit|operator`), expiry, and non-identifying lineage id.

### Local sync and supported MCP Clients

Optional: if you hold an invite token for the public Hub, provided to the process through an approved runtime injector, create `<content-home>/_local/hub/config.json` (local only, never exported). Without it the local CLI keeps working and `hub sync` reports "Hub not configured":

```json
{"schema_version":1,"active":"public","hubs":{"public":{"url":"https://public-hub.example.org","token_env":"PORTWRIGHT_PUBLIC_HUB_TOKEN"}}}
```

The URL must be a bare HTTPS origin; the placeholder above is not a real Hub. `token_env` is an environment-variable **name**, never its value; supply the credential to the CLI and local MCP process at runtime. A separate `company` entry is reserved for a company Hub, which is not currently approved or available. No personal Hub sync.

Without `--hub`, catalog, preflight, MCP reads and submissions use `config.active`. If the config omits `active`, local tracked and private notes still work, but no Hub is selected by default, even after an explicit sync; a present but invalid `active` is rejected. A one-off `hub sync --hub company` updates only the company snapshot; it does not change the public default. To switch the default, change `active` explicitly. A damaged cached note fails offline reads closed, but an online sync can replace it after validating the saved recall projection and a fresh Hub response. The next sync lock owner removes only proven inactive generations and incomplete generations belonging to that Hub.

```sh
"$PW/bin/portwright" hub sync --hub public --home "$PORTWRIGHT_HOME"
"$PW/bin/portwright" preflight acme --hub public --home "$PORTWRIGHT_HOME"
"$PW/bin/portwright" hub sync --offline --home "$PORTWRIGHT_HOME"
```

`hub sync --include-trial` opts in once. Set `"include_trial":true` inside that Hub's config entry for persistent opt-in; otherwise trial is not stored or shown. An offline sync verifies the active snapshot and previously received recalls without a network request or token lookup. Preflight reports the last successful sync time; it does not claim current online recall freshness. If a sync is interrupted, leave `<content-home>/_local/hub/sync.lock` untouched while any sync process may still be running. Remove a leftover lock only after independently confirming that no process owns it.

For a supported MCP Client, register the local stdio command `"$PW/bin/portwright" mcp --home "$PORTWRIGHT_HOME"` (with absolute paths filled in, since a Client's settings do not expand your shell variables) using the Client's own MCP configuration; invoke its `preflight`, `get_note`, and `status` tools. With a configured Hub and runtime token, its three screened submission tools also appear. Do not paste token values into Client settings, commands, or URLs. A Client connected directly to a remote Hub bypasses local screening; the remote Hub's screening is still mandatory.

The notes MCP above (`portwright mcp`) is not the skills MCP. `"$PW/bin/portwright" skills serve --home "$PORTWRIGHT_HOME"` is a different local stdio server (`portwright-skills`) that serves skill folders listed in `<content-home>/_local/skills.json` and, only when a personal Hub is configured, the personal Hub's skills. Register each separately; neither needs a remote configuration to run locally. Here `PW` is the clone directory and `PORTWRIGHT_HOME` is your content home, as defined in the README.

## Boundaries and protection

Portwright records how to use external tools, not their credentials or raw outputs. Its intended submission contract is **local pre-check on supported submission paths + central re-check before persistent storage or model transmission + no retention of rejected raw text**. These are design requirements, not certification of the current deployment: live SECRETS/INJECTION acceptance remains incomplete. Local synthetic checks do not establish central-side leak prevention or complete rejection coverage. Screening is pattern-based and cannot detect every possible secret or identifier. Do not submit credential values, identifying information, raw files, or logs. The private work repository, personal skills, and personal profiles are not shared content.

The design prohibits storing or transmitting users' external-service token, API-key, or environment-variable values; executing their external tool calls on their behalf; acting as a gateway or enforcing tool-call policy; collecting user-identifying information; and accepting raw file/log uploads. Invite-token authentication is the narrow exception for a Hub access credential sent in an authentication header. The Hubs authenticate intake and check publication, which does not make them gateways for external tool calls. Policy enforcement, a gateway, and an audit pipeline remain deferred. Planned combined model spending across the public and company Hubs is capped at USD 20 per month by reserving cost before each model call; Cloudflare and hourly, off-the-hour Actions are planned within free tiers. Budget exhaustion stops model analysis and new publishing, not reads or recall; platform-free-tier exhaustion is not a guarantee of continued online availability.
