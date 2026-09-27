# Production deployment guide

The service remains one modular-monolith deployment. Run the API and, when needed, a worker process from the same image; scale them independently without splitting domain ownership.

## Database and pgvector

Use managed PostgreSQL 15+ with TLS, private networking, encrypted storage, PITR, and a least-privilege application role. Set `DATABASE_URL=postgresql+asyncpg://...`. Run `uv run alembic upgrade head` as a release job, not concurrently from every replica. Enable `CREATE EXTENSION IF NOT EXISTS vector` in a controlled migration. The reference model uses JSON embeddings to keep SQLite tests portable; a production migration can add `embedding_vector vector(1536)` (matching the selected embedding model), backfill it, and create an HNSW/IVFFlat index. Keep `workspace_id` in the predicate even when using vector search.

Use a connection pooler for high replica counts and monitor pool saturation, lock time, slow queries, index size, and migration duration. Never use `Base.metadata.create_all` as the production schema migration mechanism.

## Object storage

Set `STORAGE_BACKEND=s3`, `S3_BUCKET`, region, and optional S3-compatible endpoint. Prefer AWS IAM roles/workload identity or an equivalent short-lived identity; do not put access keys in API requests. Use private buckets, TLS, server-side encryption/KMS, versioning, lifecycle retention, access logs, and object-lock policy as required. Store only generated workspace-scoped keys in the database. Add antivirus/content disarm and isolated PDF parsing before enabling binary formats.

## Redis and jobs

Set `REDIS_URL` to a managed TLS Redis for atomic distributed rate limits and cache hooks. For asynchronous processing, implement the `JobQueue` adapter with a durable queue/worker (RQ, Dramatiq, Celery, SQS, or a cloud-native equivalent) and select `QUEUE_BACKEND=redis` only when the worker contract is deployed. Jobs must be idempotent by document ID, retry with backoff, bound concurrency, and move poison payloads to a dead-letter queue. Inline mode is the deterministic local fallback.

## Secrets and identity

Disable dev auth (`ENVIRONMENT=production`, `ALLOW_DEV_AUTH=false`). Configure OIDC issuer/audience and JWKS URL or a pinned rotated public key. Put provider API keys, database credentials, and cloud identity configuration in a secret manager. Rotate keys, restrict LLM model permissions, and record only metadata such as provider name/latency—not prompts, completions, or keys—in telemetry.

## Backups, monitoring, and SLOs

Enable encrypted, tested PostgreSQL backups/PITR and a separately encrypted object-store backup/replication policy. Test restores at least quarterly, including application migrations and document/object references. Expose `/metrics` to Prometheus; scrape request rate/latency/status, readiness, provider errors/latency, queue age, processing failures, rate-limit responses, quota use, DB pool health, and citation verification/abstention ratios. Send traces to OpenTelemetry-compatible infrastructure with content redaction and bounded attributes. Start with the SLO suggestions in the README and tune from real traffic.

## Release and rollback

1. Build an immutable image with a pinned `uv.lock`, run offline tests, Ruff, Pyright, and a dependency/image scan.
2. Apply backward-compatible migrations, then deploy the API with a readiness gate and a canary replica.
3. Verify health, key API contracts, queue age, provider errors, and tenant isolation before increasing traffic.
4. Roll back the image if needed. Do not automatically downgrade migrations; use a forward fix. Keep old columns/fields until all old binaries are gone.
5. For data incidents, disable affected provider/queue paths, preserve audit metadata, revoke exposed credentials, and follow `SECURITY.md` incident handling. Document deletion effects on backups and legal retention.
