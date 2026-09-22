---
service: supabase
date: 2026-07-26
service_version: "Supabase CLI via npx (2026-07, exact version unrecorded)"
status: active
distributable: true
---

# Supabase CLI database password echoed by npx notice

## Symptom

Running a Supabase CLI command through an unsilenced `npx` invocation printed the full
command line. The database password supplied with `--password` was therefore exposed in
tool output even though the command itself did not log credentials.

## Root cause

The output came from npm/npx package execution notice handling, not from Supabase's
database client.

## Confirmed recovery

Rotate the database password, update the credential store, and verify the replacement
with `npx --yes --loglevel silent supabase@<pinned-version> ... --password "$DB_PASSWORD"`.
Never repeat the exposed value in reports or logs.

