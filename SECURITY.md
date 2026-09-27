# Security policy and threat model

## Reporting

Please do not disclose a vulnerability in a public issue. Send a minimal reproduction, affected version/commit, impact, and suggested mitigation to the maintainers through the private security channel configured by the deploying organization. Do not include customer documents, credentials, or tokens. We aim to acknowledge reports within five business days.

## Trust boundaries

1. Internet/client to FastAPI: all headers, multipart fields, questions, and IDs are hostile input.
2. Identity provider to API: only verified OIDC JWT claims are trusted; `sub` is the stable identity key.
3. Workspace to workspace: membership is the authorization boundary. Every resource query carries the authenticated workspace ID.
4. Document to model: uploaded text is untrusted data and can contain prompt injection, secrets, malware-like strings, or misleading citations.
5. API to adapters: object storage, Redis, queues, and LLM providers are external dependencies and must not receive tenant data outside the requested operation.
6. Database/object store/backups: operators and service identities are privileged; encrypt and audit access.

## Main controls

- No password authentication or password hashes are implemented. Local API keys are disabled outside development/test; production uses signature, issuer, and audience verification with JWKS/key rotation.
- Missing membership and missing resources intentionally share a 404 response. Roles are checked for owner/admin operations.
- Documents are UTF-8 text only in the local processor, bounded by bytes and characters, stored under generated workspace-scoped keys, and served as metadata only. Filenames are sanitized.
- Documents/chunks are soft-delete aware; document deletion removes the object and chunk rows. Backups require a documented retention and erasure process.
- Provider output is not trusted: citation IDs are intersected with the current retrieval set. No-evidence responses abstain.
- Rate-limit and usage-quota adapters are explicit seams; use Redis atomics across replicas. Request IDs are bounded and secrets/content are excluded from audit metadata.
- Production must use TLS, encrypted Postgres and object storage, least-privilege IAM, network egress controls, secret rotation, dependency scanning, and regular restore drills.

## Residual risks

The local SQLite/filesystem mode is for development and tests, not multi-replica production. A remote LLM may still leak data if tenant/provider policy is misconfigured; use provider enterprise controls, redaction, region pinning, and a documented data processing agreement. Prompt-injection defenses reduce but do not eliminate model risk; retain human review for consequential actions. Add malware scanning and PDF sandboxing before enabling more file types.
