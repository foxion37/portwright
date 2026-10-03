# Adapter — Cursor

Cursor stores global User Rules inside the application, not in a documented
user-level file. This is the one Client adapter that requires a human-only step.

`$PW` is the absolute path of your clone, as set in the [install guide](../README.md) (`PW=...`); always quote it, e.g. `"$PW/bin/portwright"`. The installed text records that path, so re-run install after moving the clone. This adapter installs guidance only; it does not register an MCP server or launch the Client.

## Prepare install

```sh
"$PW/bin/portwright" client install cursor
```

The command prints a ready-to-paste block with the current Portwright path.
The command exits 0 with `[ACTION-REQUIRED]` and changes no file. The manual step cannot be automated: User Rules live inside the Cursor application, not in a documented user-level file. Open Cursor Settings → Rules → User Rules and paste that block once.

The managed block uses these markers:

```text
<!-- portwright:start (managed block — safe to remove between these markers) -->
<!-- portwright:end -->
```

## Verify

```sh
"$PW/bin/portwright" client doctor cursor
```

The CLI always reports `[MANUAL]` (it cannot read the setting) because Cursor does not expose User Rules as a file.

## Remove

Run `"$PW/bin/portwright" client remove cursor` (prints a reminder only), then remove the marked block in User Rules.
