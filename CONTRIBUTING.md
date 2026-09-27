# Contributing

1. Use Python 3.12+ and uv.
2. Work only inside this repository and create a focused branch/commit.
3. Install dependencies with `uv sync`.
4. Before opening a change, run:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv lock --check
```

Keep tenant authorization explicit at the router/service boundary, add a negative cross-workspace test for new workspace resources, avoid logging secrets/raw documents, and update migrations/API examples for contract changes. Prefer deterministic local providers in tests; external provider tests must be opt-in and use redacted fixtures. Do not add passwords or credentials to the data model.
