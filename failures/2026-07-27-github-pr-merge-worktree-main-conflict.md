---
date: 2026-07-27
service: github
service_version: "GitHub CLI gh pr merge (2026-07, exact version unrecorded)"
status: active
distributable: true
---

# `gh pr merge` can merge remotely and then fail during local worktree cleanup

## Symptom

Running `gh pr merge <number> --squash --delete-branch` inside a linked Git
worktree printed:

```text
failed to run git: fatal: 'main' is already used by worktree at '<path>'
```

The pull request had already been merged on GitHub. Only the local checkout and
branch-cleanup phase failed.

## Root cause

`gh pr merge` attempted a local `main` checkout after completing the remote
merge. Git rejected that checkout because another linked worktree already used
`main`.

## Verified procedure

When `main` may be checked out in another worktree, run the merge from a neutral
directory and identify the repository explicitly:

```sh
cd /tmp
gh pr merge <number> --repo <owner>/<repo> --squash --delete-branch
```

Always verify the remote result before retrying:

```sh
gh pr view <number> --repo <owner>/<repo> --json state,mergedAt,mergeCommit,url
```

If the PR is already merged and the remote head branch remains, delete only the
remote branch. Do not retry a second merge or switch the current worktree to
`main`.
