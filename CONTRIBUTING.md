# Contributing

## Setup

Python work happens in `autocontext/`:

```bash
cd autocontext
uv venv
source .venv/bin/activate
uv sync --group dev
```

Optional extras:

```bash
uv sync --group dev --extra mcp
uv sync --group dev --extra mlx
uv sync --group dev --extra monty
```

TypeScript work happens in `ts/`:

```bash
cd ts
npm install
```

## Common Checks

Python:

```bash
cd autocontext
uv run ruff check src tests
uv run mypy src
uv run pytest
```

TypeScript:

```bash
cd ts
npm run lint
npm test
```

TUI:

```bash
cd tui
npm install
npm test
```

## Development Notes

- The Python package name and CLI are `autocontext` / `autoctx`.
- Environment variables use the `AUTOCONTEXT_` prefix.
- Prefer targeted tests for touched modules before running full suites.
- Keep protocol changes in sync with `scripts/generate_protocol.py`.
- Avoid rewriting historical plan docs unless the change is user-facing or release-facing.

## Pull Requests

- Keep changes scoped to one feature or cleanup theme.
- Prefer reviewable PRs. If a change is broad, split it into preparatory cleanup and feature follow-ups.
- Avoid mixing renames, refactors, and behavior changes in the same PR unless the branch would be unmergeable otherwise.
- Update docs and examples when renaming commands, env vars, or package paths.
- Include verification notes for the checks you ran.

## Repository Rules

- `main` is intended to stay protected and merge-only.
- Contributor PRs should pass `lint`, `test`, and `smoke` before merge.
- Open a draft PR early for large work so maintainers can steer scope before review.
- Keep local workspace artifacts, assistant scratch files, and private notes out of the repo. The root `.claude/` directory is intentionally ignored.
