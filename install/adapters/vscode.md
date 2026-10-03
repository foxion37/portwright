# Adapter — VS Code Copilot

VS Code loads user-level `*.instructions.md` files from
`~/.copilot/instructions/`. The Portwright adapter owns the dedicated file
`~/.copilot/instructions/portwright.instructions.md`, adds the required
`applyTo: "**"` frontmatter, and installs one managed block.

VS Code documents the profile location and `applyTo` requirement in its
[custom instructions guide](https://code.visualstudio.com/docs/copilot/customization/custom-instructions).

`$PW` is the absolute path of your clone, as set in the [install guide](../README.md) (`PW=...`); always quote it, e.g. `"$PW/bin/portwright"`. The installed text records that path, so re-run install after moving the clone. This adapter installs guidance only; it does not register an MCP server or launch the Client.

## Install

```sh
"$PW/bin/portwright" client install vscode
```

The managed block is bounded by these markers:

```text
<!-- portwright:start (managed block — safe to remove between these markers) -->
<!-- portwright:end -->
```

If the dedicated file already exists without the required frontmatter, the
installer refuses to modify it. This prevents an unrelated user file from
being silently repurposed.

## Verify

```sh
"$PW/bin/portwright" client doctor vscode
```

## Remove

```sh
"$PW/bin/portwright" client remove vscode
```

Text and frontmatter outside the managed block are preserved.
