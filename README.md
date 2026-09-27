# Knowledge SaaS API

A production-oriented, tenant-isolated AI knowledge SaaS implemented as a **modular monolith**. It is a FastAPI service with explicit domain boundaries for identity, workspaces, documents, retrieval/answers, conversations, usage, audit, storage, providers, jobs, and observability. The default local path is deterministic and needs no API keys, Postgres, Redis, or cloud account.

## Architecture

```text
                         +-----------------------------+
                         | FastAPI /v1 HTTP boundary   |
                         | request ID, limits, auth    |
                         +-------------+---------------+
                                       |
       +-------------------------------+-------------------------------+
       |               |                |                 |              |
  Identity &      Workspace &       Documents        Retrieval &      Conversations
  OIDC/API key    membership       upload/process    answers         feedback
       |               |                |                 |              |
       +---------------+----------------+-----------------+--------------+
                                       |
                    domain services + SQLAlchemy 2 models
                                       |
       +------------------+------------+-------------+------------------+
       |                  |                          |
  SQLite dev /       Postgres + pgvector       ObjectStorage          Redis
  test fallback      production                local/S3                limit/cache
                                       |
                         Inline jobs / queue adapter
``` 

The process is one deployable application. Provider, object-storage, rate-limit/cache, and job interfaces are replaceable adapters; no microservices are required. SQLite stores embeddings as JSON for offline tests. Production deployments can enable the pgvector extension and migrate the embedding column to `vector(n)` for indexed ANN search (see `docs/DEPLOYMENT.md`).

## Quick start

Requires Python 3.12+ (3.13 recommended) and [uv](https://docs.astral.sh/uv/).

```bash
cd apps/05-ai-saas-foundation
uv sync
cp .env.example .env
uv run uvicorn knowledge_api.main:app --app-dir src --reload
```

Check health:

```bash
curl http://127.0.0.1:8000/v1/health/live
curl http://127.0.0.1:8000/v1/health/ready
```

The local database is `data/knowledge.db` and objects are in `data/objects/`. The app creates development tables on startup; production uses Alembic:

```bash
uv run alembic upgrade head
```

Offline quality commands:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv lock --check
```

## Local end-to-end flow

Local development authentication is an API key, not a password system. Create a user, then send the returned `user.id` as `X-Dev-User-Id` with the dev bearer token.

```bash
USER=$(curl -s -X POST http://127.0.0.1:8000/v1/auth/dev/users \
  -H 'Content-Type: application/json' \
  -d '{"email":"owner@example.test","display_name":"Owner"}')
USER_ID=$(echo "$USER" | jq -r .user.id)
AUTH=(-H 'Authorization: Bearer dev-local-token' -H "X-Dev-User-Id: $USER_ID")
WORKSPACE=$(curl -s -X POST http://127.0.0.1:8000/v1/workspaces "${AUTH[@]}" \
  -H 'Content-Type: application/json' -d '{"name":"Acme Knowledge"}')
WS=$(echo "$WORKSPACE" | jq -r .id)
curl -s -X POST "http://127.0.0.1:8000/v1/workspaces/$WS/documents" "${AUTH[@]}" \
  -F 'file=@examples/handbook.txt;type=text/plain'
curl -s -X POST "http://127.0.0.1:8000/v1/workspaces/$WS/search" "${AUTH[@]}" \
  -H 'Content-Type: application/json' -d '{"query":"retention period","top_k":5}'
curl -s -X POST "http://127.0.0.1:8000/v1/workspaces/$WS/answers" "${AUTH[@]}" \
  -H 'Content-Type: application/json' -d '{"question":"What is the retention period?"}'
```

Upload processing is inline by default so the flow is deterministic. `POST .../documents/{id}/process` is available for a worker or a retry. Answers cite only chunks returned from the same workspace query and abstain when evidence is missing.

## API contract

All protected workspace resources require a verified identity **and** membership in the path workspace. A missing or cross-tenant workspace returns the same 404 boundary. Every response has `X-Request-Id` (accepted incoming IDs are bounded to 100 characters).

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/health/live` | process liveness |
| GET | `/v1/health/ready` | database readiness |
| POST | `/v1/auth/dev/users` | local-only user bootstrap |
| POST | `/v1/auth/dev/token` | local-only token lookup by email |
| GET | `/v1/me` | authenticated identity |
| POST/GET | `/v1/workspaces` | create/list workspaces |
| GET/DELETE | `/v1/workspaces/{workspace_id}` | metadata/soft-delete (owner) |
| GET/POST | `/v1/workspaces/{workspace_id}/members` | membership management |
| POST/GET | `/v1/workspaces/{workspace_id}/documents` | upload/list metadata |
| GET/POST/DELETE | `/v1/workspaces/{workspace_id}/documents/{document_id}` | status/process/soft-delete |
| POST | `/v1/workspaces/{workspace_id}/search` | workspace retrieval with citations |
| POST | `/v1/workspaces/{workspace_id}/answers` | evidence-grounded answer/abstention |
| POST/GET | `/v1/workspaces/{workspace_id}/conversations` | conversation ownership |
| GET/POST | `/v1/workspaces/{workspace_id}/conversations/{id}/messages` | messages |
| POST | `/v1/workspaces/{workspace_id}/messages/{id}/feedback` | feedback |
| GET | `/v1/workspaces/{workspace_id}/usage` | quota/usage summary |
| GET | `/v1/workspaces/{workspace_id}/audit-events` | metadata-only audit trail |
| GET | `/metrics` | Prometheus-compatible metrics |

Document uploads accept bounded UTF-8 `text/plain`, Markdown, JSON, or CSV. Filenames are reduced to a safe basename; object keys are generated server-side. `Idempotency-Key` makes upload retries return the original metadata within a user/workspace scope. Raw documents are never placed in audit metadata or application logs.

## Authentication and security boundary

- Development/test: `Authorization: Bearer $DEV_API_KEY` plus `X-Dev-User-Id` (or `X-API-Key`). The dev bootstrap endpoints are disabled when `ENVIRONMENT` is not development/test.
- Production: configure an OIDC issuer/audience and `OIDC_JWKS_URL`, or a pinned `JWT_PUBLIC_KEY`; the verifier only accepts asymmetric RS256/ES256 signatures. Tokens are mapped to a user by immutable `sub`. There is no password table and no insecure password storage.
- Membership authorization is performed before every workspace resource query. Queries include `workspace_id` and active/soft-delete predicates; document IDs are never looked up globally.
- Data is soft-deleted at workspace/document boundaries. Document object and chunks are removed immediately on document deletion; a production retention/purge worker should handle regulated erasure and backups.
- Input sizes, filenames, content types, questions, message lengths, top-k, idempotency keys, and audit page sizes are bounded. Rate limits and a monthly usage-quota hook are enforced before expensive answer work.
- Document text is untrusted prompt context. Provider prompts state that it is data, not instructions; deterministic synthesis excludes instruction-shaped lines; provider citations are allow-listed against retrieved chunk IDs.
- No request middleware logs authorization headers, API keys, raw content, or provider payloads. Secrets belong in a secret manager/environment, never source control.

See `SECURITY.md` for reporting and the full threat model.

## Evaluation and testing

The test suite uses SQLite, local filesystem storage, NumPy hashing embeddings, and a deterministic evidence-only provider. It covers liveness/readiness, API-key auth, tenant isolation and negative cross-workspace access, upload/processing, search citations, answer citations and abstention, prompt-injection-shaped text, rate-limit hooks, audit records, idempotency, feedback, and an offline end-to-end flow.

For retrieval evaluation, add a fixture of question/expected chunk IDs and measure recall@k, citation precision, abstention precision, and answer groundedness. Run the deterministic suite on every change; run a separately gated provider evaluation with redacted production-like documents and no secrets in CI.

## SLO suggestions

Start with 99.9% monthly availability for live API requests, p95 metadata/search latency under 300 ms on a warm production index, p95 answer latency under 8 s for the selected provider, 99% successful document processing within 5 minutes, and 100% audit coverage for mutations. Alert on readiness failures, queue age, processing failure rate, citation verification failures, quota/rate-limit spikes, provider error rate, and backup restore test age.

## Deployment

See `docs/DEPLOYMENT.md` for PostgreSQL/pgvector, S3-compatible storage, Redis, queue workers, secrets, backups, monitoring, migrations, and rollback. `docker-compose.yml` starts the local API, Postgres with pgvector, Redis, and the API; offline tests do not require those services.

## License

MIT; see `LICENSE`.
