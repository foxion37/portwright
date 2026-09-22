# Portwright Tool Use Checklist

Use this quick checklist when an external tool appears.

1. What is the lowercase kebab-case Service id?
2. Did `portwright preflight <service-id>` return the Procedure and active Lessons?
3. Is the intent `call`, `recover`, or `instruct`?
4. For `instruct`, was the human-only path checked against current official docs?
5. Is the Agent doing every Agent's job itself?
6. Did a confirmed new Failure emerge that needs a draft via `portwright memory draft lesson`?
