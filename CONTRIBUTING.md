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

TUI-related TypeScript work:

```bash
cd ts
npm install
npm test
```

## Repo Map

- `autocontext/`: Python package, CLI, API server, and tests
- `ts/`: published TypeScript package, Node CLI, MCP server, and bundled Ink terminal UI
- `scripts/`: repo maintenance and protocol generation helpers

## Development Notes

- The Python package name and CLI are `autocontext` / `autoctx`.
- Environment variables use the `AUTOCONTEXT_` prefix.
- Prefer targeted tests for touched modules before running full suites.
- Keep protocol changes in sync with `scripts/generate_protocol.py`.
- Avoid rewriting historical plan docs unless the change is user-facing or release-facing.

## Documentation Touch Points

When a change affects public commands, environment variables, package names, or agent-facing workflows, update the relevant docs in the same PR:

- `README.md`
- `docs/README.md`
- `autocontext/README.md`
- `ts/README.md`
- `examples/README.md`
- `autocontext/docs/agent-integration.md`
- `AGENTS.md`
- `CHANGELOG.md`

## Releases

Publishing is split by package and uses GitHub OIDC trusted publishing rather than long-lived PyPI or npm tokens.

- Python publishes through `.github/workflows/publish-python.yml`
  - tag trigger: `py-v<version>`
  - manual trigger: `workflow_dispatch` from `main`
  - environment: `publish-python`
- TypeScript publishes through `.github/workflows/publish-ts.yml`
  - tag trigger: `ts-v<version>`
  - manual trigger: `workflow_dispatch` from `main`
  - environment: `publish-ts`

Release notes:

- Keep the GitHub environment branch/tag policy restricted to `main` and the matching tag namespace.
- The trusted publisher registration in PyPI and npm must match the repo, workflow filename, and environment name exactly.
- No `NPM_TOKEN`, `NODE_AUTH_TOKEN`, or PyPI API token should be required for the publish jobs.
- After cutover, remove the old combined `.github/workflows/publish.yml` publisher registration from PyPI and npm.

## Type System Conventions

### ABC vs Protocol

- **ABC** — for internal class hierarchies where subclasses share implementation via inheritance (e.g., `ScenarioInterface`, `LLMProvider`, `AgentRuntime`, `Notifier`)
- **Protocol** — for duck-typed integration points where implementors shouldn't need to import the base class (e.g., `ExecutionEngine`, `Evaluator`, `DictSerializable`, `ReplWorkerProtocol`)
- New root ABCs (`class X(ABC)`) should define at least one `@abstractmethod`; subclasses that inherit an abstract contract from another ABC do not need to redeclare one.

### Dict types

- Use `dict[str, Any]` for JSON-like dicts (not `dict[str, object]`)
- Prefer `TypedDict` when the dict shape is known at all call sites
- Use `Mapping[str, Any]` for read-only dict parameters

### Collection parameters

- Use `Sequence[X]` for read-only list parameters in public API functions
- Use `list[X]` for return types and parameters that are mutated
- Use `Mapping[str, X]` for read-only dict parameters (already used in `ScenarioInterface`)

### Type aliases

- `LlmFn = Callable[[str, str], str]` — defined in `agents/types.py`
- Use `from enum import StrEnum` (not `import enum` + `enum.StrEnum`)

### Logger naming

- Use `logger = logging.getLogger(__name__)` (lowercase, per PEP 8)

## Pull Requests

- Keep changes scoped to one feature or cleanup theme.
- Update docs and examples when renaming commands, env vars, or package paths.
- Include verification notes for the checks you ran.
