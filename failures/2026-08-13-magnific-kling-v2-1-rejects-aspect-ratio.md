---
date: "2026-08-13"
service: magnific
service_version: "Magnific Kling v2.1 Standard REST API 2026-08-13"
status: active
distributable: true
---

## What was attempted

An image-to-video request sent `image`, `prompt`, string `duration`, and `aspect_ratio` to `/v1/ai/image-to-video/kling-v2-1-std`.

## Failure

The upload succeeded, but the create request returned HTTP 400 `Validation error`; no video task was created.

## Root cause

Unlike the Kling O1 interpolation endpoint, the verified Kling v2.1 Standard request schema does not accept `aspect_ratio` for image-to-video. The input image already defines the frame ratio.

## Correct procedure

Send `duration`, `image`, `prompt`, optional `negative_prompt`, and `cfg_scale`. Do not send `aspect_ratio` to Kling v2.1 Standard image-to-video.
