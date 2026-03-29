# Agent Guide

Use this file as the first pointer for coding agents and automation that need to work in or against this repo.

## Pick The Right Surface

- Full autocontext control plane: `autocontext/`
- Node/TypeScript toolkit and CLI: `ts/`
- Interactive terminal UI: `ts/src/tui/`
- External agent usage guide: `autocontext/docs/agent-integration.md`

## Working Directories

- Run Python commands from `autocontext/`.
- Run Node/TypeScript commands from `ts/`.
- Run TUI-related commands from `ts/`.

Most repo-level commands in the READMEs assume one of those package directories as the current working directory.

## Setup

Python:

```bash
cd autocontext
uv venv
source .venv/bin/activate
uv sync --group dev
```

TypeScript:

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

## Public Docs To Keep In Sync

If you change public commands, environment variables, package names, or agent-facing workflows, update the relevant docs in the same change:

- `README.md`
- `docs/README.md`
- `autocontext/README.md`
- `ts/README.md`
- `examples/README.md`
- `autocontext/docs/agent-integration.md`
- `CONTRIBUTING.md`
- `CHANGELOG.md`

## Key References

- Repo overview: `README.md`
- Docs overview: `docs/README.md`
- Contributor workflow: `CONTRIBUTING.md`
- Python package guide: `autocontext/README.md`
- TypeScript package guide: `ts/README.md`
- Copy-paste examples: `examples/README.md`
- External agent guide: `autocontext/docs/agent-integration.md`
