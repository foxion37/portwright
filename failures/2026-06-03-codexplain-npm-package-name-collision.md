---
date: "2026-06-03"
service: codexplain
service_version: "NomaDamas/Codexplain 0.18.18 vs npm codexplain@1.0.2 (2026-06-03)"
status: active
distributable: true
---

# Codexplain npm package name collision

## Symptom

When installing `NomaDamas/Codexplain`, `npm install -g codexplain` (as of
2026-06-03) installed an unrelated npm package `codexplain@1.0.2` with a
Chinese test-tool package.json and a broken `index.js` that throws
`TypeError [ERR_INVALID_ARG_TYPE]: The "path" argument must be of type string ... Received undefined`.

## 진짜 원인 (root cause)

npm package-name collision: the npm registry name `codexplain` is squatted by an
unrelated package. The real project is distributed via a Homebrew tap, not npm.

## Fix (verified)

Uninstall the npm package, then use the GitHub tap from the project README:

```bash
npm uninstall -g codexplain
brew tap NomaDamas/Codexplain https://github.com/NomaDamas/Codexplain
brew install codexplain
```

The Homebrew formula installed `codexplain 0.18.18` and exposed
`/opt/homebrew/bin/codexplain`.
