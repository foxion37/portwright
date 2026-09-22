---
service: supabase
date: 2026-07-26
service_version: "Supabase Auth (GoTrue) password grant, 2026-07"
status: active
distributable: true
---

# Supabase password grant blocked for a headless worker by CAPTCHA

## Symptom

`POST /auth/v1/token?grant_type=password` returned HTTP 400 with
`captcha_failed` and `request disallowed (no captcha_token found)` for a dedicated
background worker.

## Root cause

Project-wide CAPTCHA protection correctly required a human challenge for password
login. A launchd worker cannot complete that challenge.

## Confirmed recovery

Keep CAPTCHA enabled. Use the admin API once to generate a magic-link token for the
dedicated Auth user, exchange the returned hash at `/auth/v1/verify`, then authenticate
future worker runs with `grant_type=refresh_token`. Persist each rotated refresh token in
a local mode `0600` file. The resulting user JWT continues to use RLS and avoids placing
the service-role key on the worker host.

