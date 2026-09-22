---
date: 2026-06-19
service: github
service_version: "GitHub CLI gh api (2026-06, exact version unrecorded)"
status: active
distributable: true
---

# gh api deployments query accidentally sent POST

## Failure (root cause)

`gh api repos/<owner>/<repo>/deployments -f ref=<branch>` was intended to query
deployments, but `gh api` treats field flags as a request body and switched the
call into a deployment creation request. GitHub rejected it with HTTP 409 because
required status checks were still pending.

## Correct procedure

For deployment lookup, force GET and put filters in the URL:

```sh
gh api -X GET "repos/<owner>/<repo>/deployments?ref=<branch>"
```

Do not use `-f ref=...` for read-only deployment queries unless the HTTP method
is explicitly pinned and verified.
