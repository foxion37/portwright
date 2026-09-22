---
date: 2026-06-03
service: github
service_version: "GitHub CLI gh api graphql / GraphQL UserList mutation"
status: active
distributable: true
---

# GitHub GraphQL array variable

## 무엇을 시도했나
`gh api graphql`로 `updateUserListsForItem(input:{itemId,listIds})`를 호출해 GitHub Star List membership을 갱신했다.

## 어떤 에러가 났나
Python에서 GraphQL 변수 `listIds`를 JSON 문자열로 넘겼다:

```bash
gh api graphql ... -F 'listIds=["UL_..."]'
```

GitHub는 배열이 아니라 문자열 global id로 해석했고 다음 오류가 났다:

```text
Could not resolve to a node with the global id of '["UL_..."]'
```

## 진짜 원인
`gh api graphql -F`에서 GraphQL list variable은 JSON 문자열 하나가 아니라 반복 array parameter 형태로 넘겨야 한다.

## 고친 방법 (다음엔 이대로)
배열 변수는 `key[]=value` 형태로 반복한다:

```bash
gh api graphql \
  -f query='mutation($itemId:ID!, $listIds:[ID!]!) { updateUserListsForItem(input:{itemId:$itemId,listIds:$listIds}) { lists { id name } } }' \
  -F itemId="$REPO_ID" \
  -F 'listIds[]=UL_kwDO...'
```

여러 list id는 `-F 'listIds[]=ID1' -F 'listIds[]=ID2'`처럼 반복한다.
