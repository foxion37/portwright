# Adapter — VS Code Copilot

VS Code loads user-level `*.instructions.md` files from
`~/.copilot/instructions/`. The Portwright adapter owns the dedicated file
`~/.copilot/instructions/portwright.instructions.md`, adds the required
`applyTo: "**"` frontmatter, and installs one managed block.

VS Code documents the profile location and `applyTo` requirement in its
[custom instructions guide](https://code.visualstudio.com/docs/copilot/customization/custom-instructions).

## Install

```sh
~/developer/tools/portwright/bin/portwright client install vscode
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
~/developer/tools/portwright/bin/portwright client doctor vscode
```

## Remove

```sh
~/developer/tools/portwright/bin/portwright client remove vscode
```

Text and frontmatter outside the managed block are preserved.
