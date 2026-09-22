---
date: "2026-09-15"
service: magnific
service_version: "Native Magnific MCP, Kling 2.5 catalog observed 2026-09-15"
status: active
distributable: true
---

## What was attempted

Three 8-second Kling 2.5 start/end-frame video jobs were submitted through the Magnific MCP after `video_plan` proposed 6-second clips.

## Failure

The requests were accepted into the queue, but all three later reached terminal failure with `duration value '8' is invalid`.

## Root cause

The current `video_models_list` entry for `kling-25` exposes only 5- and 10-second durations. Queue acceptance did not enforce that catalog constraint before backend processing.

## Correct procedure

For `kling-25`, use exactly 5 or 10 seconds even if `video_plan` proposes another duration. To deliver an 8-second segment while preserving both endpoint keyframes, generate 10 seconds and retime the complete clip to 8 seconds during deterministic post-production. Do not trim away the end frame.
