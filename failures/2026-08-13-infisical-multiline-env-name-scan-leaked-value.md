---
date: "2026-08-13"
service: infisical
service_version: "Infisical CLI 0.43.104 (macOS)"
status: active
distributable: true
---

## What was attempted

The agent tried to list only injected environment-variable names with a line-oriented `env | cut -d= -f1` pipeline while locating a media API key.

## Failure

A JSON credential stored as a multiline environment value contained embedded newline characters. Lines inside the value bypassed the intended `NAME=` extraction and part of a private key was emitted to command output.

## Root cause

POSIX `env` output is not a safe serialization when values may contain newlines. A downstream line parser cannot distinguish a new variable from a continuation line inside a multiline value.

## Correct procedure

Inspect names inside the process that already has the injected environment, for example with Python: iterate over `os.environ` keys and print only matching keys. Never serialize the full environment first. If this failure occurs, do not repeat the exposed value and rotate the affected credential because the command transcript is no longer a safe location.
