---
date: "2026-08-13"
service: magnific
service_version: "Magnific Kling O1 Pro REST API 2026-08-13"
status: active
distributable: true
---

## What was attempted

The agent uploaded valid first and last frame images, then created a Kling O1 Pro interpolation job with `duration` encoded as the JSON number `10`.

## Failure

The upload requests returned HTTP 200, but the create request returned HTTP 400 with `Validation error` and no task was created.

## Root cause

The endpoint schema documents `duration` as an enum of string values (`"5"`, `"10"`). The numeric value failed request validation.

## Correct procedure

Send `duration` as a JSON string. Keep `aspect_ratio` as `"16:9"`, pass the uploaded asset URLs through `first_frame` and `last_frame`, and poll `/v1/ai/image-to-video/kling-o1/{task-id}`. The corrected request returned HTTP 200 and completed successfully.
