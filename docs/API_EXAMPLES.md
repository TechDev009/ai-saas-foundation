# API examples

The examples use `BASE=http://127.0.0.1:8000`, `TOKEN=dev-local-token`, and a user ID returned by `/v1/auth/dev/users`.

```bash
USER=$(curl -s -X POST "$BASE/v1/auth/dev/users" -H 'content-type: application/json' \
  -d '{"email":"a@example.test","display_name":"A"}')
UID=$(echo "$USER" | jq -r .user.id)
H=(-H "Authorization: Bearer $TOKEN" -H "X-Dev-User-Id: $UID")
WS=$(curl -s -X POST "$BASE/v1/workspaces" "${H[@]}" -H 'content-type: application/json' \
  -d '{"name":"Example"}' | jq -r .id)
DOC=$(curl -s -X POST "$BASE/v1/workspaces/$WS/documents" "${H[@]}" \
  -F 'file=@examples/handbook.txt;type=text/plain')
curl -s -X POST "$BASE/v1/workspaces/$WS/search" "${H[@]}" \
  -H 'content-type: application/json' -d '{"query":"retention period"}'
curl -s -X POST "$BASE/v1/workspaces/$WS/answers" "${H[@]}" \
  -H 'content-type: application/json' -d '{"question":"What is the retention period?"}'
```

Expected answer responses contain `abstained: false`, a generated `conversation_id`, and citations whose `chunk_id`/`document_id` belong to the requesting workspace. Questions without evidence return `abstained: true` and no citations.
