---
date: "2026-07-23"
service: 1password
service_version: "1Password CLI 2.35.0 / macOS desktop app integration"
status: active
distributable: true
---

# Revoked service-account environment token overrides desktop authentication

## What was attempted

Read a newly saved service-account token through the unlocked 1Password desktop app after revoking the old service account.

## Error

The CLI first returned `403 Forbidden (Service Account Deleted)`. After removing that authentication source for a command, unattended requests could still end with `authorization timeout` if the desktop approval was not accepted in time.

## Root cause

An inherited `OP_SERVICE_ACCOUNT_TOKEN` takes precedence over desktop app authentication even after its service account has been revoked. Removing it from launchd does not remove the copy already inherited by a running terminal or agent process. Desktop authentication also requires the app to be unlocked, CLI integration enabled, and the request approved.

## Fix

1. Run the desktop-authenticated command with `OP_SERVICE_ACCOUNT_TOKEN` removed from that command's environment.
2. Set `OP_BIOMETRIC_UNLOCK_ENABLED=true` for the command when app integration should be used.
3. Open and unlock 1Password, confirm `Settings > Developer > Integrate with 1Password CLI`, and approve the request.
4. Pipe a revealed credential directly to its protected destination without printing it. Verify only file ownership, mode, and downstream access.
5. For long-running automation, do not restore the token with `launchctl setenv`. Load it only inside the narrow process that calls `op`.
