# `services/` is a derive-on-miss cache, not a registry

`services/` holds **cached, verified Procedures**, populated on demand — not a pre-filled registry you curate by hand. A `services/<id>.md` exists only after a tool has been used: on a cache miss or stale entry, the agent derives the current procedure from the tool's official docs and writes it back. This is what makes the layer cover **every** external tool instead of only a hand-written list. Vercel is simply the first cached entry, not a seed library.

## Why (surprising without this note)
The original repo skeleton defined `services/` as a "절차 등록소 (procedure registry)" with pre-written sample files. That framing assumes you author a procedure per tool up front — impossible at "all tools" scope, and it violates the project's core principle (*minimize human hands; manual authoring is a defect*). So the meaning was changed from registry to cache while keeping the folder.

## Considered Options
- **Cache, derive-on-miss** (chosen) — covers all tools, accumulates the ones you use, zero manual authoring.
- **Registry (hand-curated)** — the original framing; rejected because it can't scale to "all tools" and adds human work.
- **No folder, pure derive-every-time** — simplest and always fresh, but re-derives on every use and accumulates nothing; rejected to keep speed + a place for `failures/` lessons to attach.

## Consequences
- The procedure-resolution rule (verify-before-instruct) must be tool-agnostic and include the derive-and-cache path. See `CONTEXT.md` (Procedure, Procedure cache, Verify-before-instruct).
- Banned terms: "registry / 등록소 / library" for `services/`.
