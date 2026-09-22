# Adapter — Cursor

Cursor stores global User Rules inside the application, not in a documented
user-level file. This is the one Client adapter that requires a human-only step.

## Prepare install

```sh
~/developer/tools/portwright/bin/portwright client install cursor
```

The command prints a ready-to-paste block with the current Portwright path.
Open Cursor Settings → Rules → User Rules and paste that block once.

The managed block uses these markers:

```text
<!-- portwright:start (managed block — safe to remove between these markers) -->
<!-- portwright:end -->
```

## Verify

```sh
~/developer/tools/portwright/bin/portwright client doctor cursor
```

The CLI reports `MANUAL` because Cursor does not expose User Rules as a file.

## Remove

Run `portwright client remove cursor`, then remove the marked block in User Rules.
