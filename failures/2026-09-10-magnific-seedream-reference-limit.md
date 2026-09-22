---
date: "2026-09-10"
service: magnific
service_version: "Magnific Seedream 5 Pro MCP 2026-09-10"
status: active
distributable: true
---

## What was attempted

A Seedream 5 Pro image generation request used six reusable character references plus six image references.

## Failure

The request failed before rendering because the resolved reference count was 12, above the model maximum of 10.

## Root cause

Reusable character and product references count toward Seedream's resolved reference-image limit after the server expands them.

## Correct procedure

Keep the total resolved references at 10 or fewer. For a six-character scene, reserve at most four additional image references for the booth, anchor frame, product, logo, or style.
