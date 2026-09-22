---
date: 2026-09-11
service: blender
service_version: Blender 5.2.1 LTS
status: active
distributable: true
---

## What was attempted
A scene-build script iterated `bpy.data.actions` and then accessed `action.fcurves` to change every keyframe interpolation mode.

## Error
Blender raised `AttributeError: 'Action' object has no attribute 'fcurves'` before rendering began.

## Root cause
Blender 5.2 can store animation in layered or slotted Actions. Those Actions do not necessarily expose the legacy top-level `fcurves` collection.

## Fix
Do not assume every Action has `fcurves`. For broad compatibility, retain Blender's default interpolation or set interpolation on each key as it is created. If global traversal is required, feature-detect the Action storage model and use the current slot/channel-bag API.
