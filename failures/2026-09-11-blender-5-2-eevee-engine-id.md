---
date: 2026-09-11
service: blender
service_version: Blender 5.2.1 LTS
status: active
distributable: true
---

## What was attempted
A background Python build script selected `BLENDER_EEVEE_NEXT`, the identifier used by earlier Blender releases.

## Error
Blender rejected the value because the available render engines were `BLENDER_EEVEE`, `BLENDER_WORKBENCH`, and `CYCLES`.

## Root cause
Blender 5.2.1 exposes Eevee as `BLENDER_EEVEE`; the older `BLENDER_EEVEE_NEXT` identifier is not present in this build.

## Fix
Set `scene.render.engine = "BLENDER_EEVEE"` first and keep `BLENDER_EEVEE_NEXT` only as a compatibility fallback for versions that expose it. Verify the actual enum or run a minimal background build before a long render.
