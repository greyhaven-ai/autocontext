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

The TypeScript package requires Node 22.19.0 or newer. Native modules
(`isolated-vm`, `better-sqlite3`) and pi-tui share the version pinned in
`ts/.nvmrc` (also applies to `pi/`, which depends on `ts/`).

## Common Checks

Python:

```bash
cd autocontext
uv run ruff check src tests
uv run mypy src
uv run pytest
uv run python ../scripts/check_markdown_links.py
```

Python CI runs four module-preserving shards, balanced by collected test count.
To reproduce one shard from `autocontext/`, run
`uv run python scripts/pytest_shard.py 1/4 -m "not live"`.
Each shard retains JUnit timings and coverage data; the required `test` job
checks all shards and combines their coverage into the existing report.
Dependency caches remain disabled in public workflows.

The experimental [Qwen reproduction](examples/weight_evolution/qwen_reproduction/README.md)
has offline contract, reconstruction, runtime, worker and scorer tests in
`tests/test_weight_*.py`. Keep normal tests synthetic: no downloaded weights,
provider credentials, external checker installation or GPU is needed. Full
checkpoint verification uses explicitly fetched assets; live inference is an
opt-in Linux/NVIDIA Docker smoke test, documented in the example. The separate
hash-locked CPU scorer environment has explicit optional integrations; normal CI
uses synthetic cohorts and injected checkers. Reproduction reports must reject
missing/duplicate shards and label fixture/smoke evidence explicitly.
The [GPU acceptance workflow](examples/weight_evolution/qwen_reproduction/GPU_ACCEPTANCE.md)
has offline orchestration tests as well. Keep real NVIDIA execution explicit;
synthetic CUDA envelopes must never be reported as live acceptance results.
The [comparison harness](examples/weight_evolution/qwen_comparison/README.md)
uses pinned synthetic inputs and prerecorded responses in `qwen_comparison/`.
Its tests require no model, downloaded data or official checker dependencies.
Preserve fixture labels, paired coverage, permitted condition changes and
practice/evaluation separation; fixture scores are not benchmark evidence.
The [editing-session fixtures](examples/weight_evolution/qwen_sessions/README.md)
exercise typed C/W/D candidate creation and sealed exports into that harness.
Preserve practice-only adapter projections, immutable candidate lineage and
finite pre-dispatch reservations, including failed and interrupted attempts
across restart. Test that sealing prevents new edits and that an ambiguous
dispatch cannot be retried automatically. Byte counts and simulated seconds
are fixture accounting; real GPU editing, benchmark practice selection and
TypeScript parity remain deferred.
The [adaptive editing fixtures](examples/weight_evolution/qwen_adaptive/README.md)
exercise the normal provider and refinement-prompt interfaces using literal
responses addressed by practice state and feedback. Preserve equal per-arm caps,
preclaimed proposal calls, failed-attempt costs, deterministic practice selection,
and the immutable selection-before-confirmation boundary. Confirmation vetoes
must never trigger another candidate or a final audit on the same experiment.
The [checkpoint-backed fixtures](examples/weight_evolution/qwen_checkpoints/README.md)
exercise real tensor-byte changes on tiny CPU safetensors. Preserve the pinned
inventory, allowed tensor scope, shape/dtype, finite-value and untouched-payload
checks. Verify checkpoint bytes before probes, on resume, and through native
selection/confirmation seals; never recreate missing completed evidence. Legacy
string handles are compatibility projections, not authoritative artifacts.
Keep these edits data-only: real model/GPU execution and arbitrary editing code
remain outside this fixture workflow.

The [CPU inference study](examples/weight_evolution/cpu_inference/README.md)
uses a separate true-inference contract; preserve the earlier synthetic fixture
schemas and pins. Keep gold labels outside worker requests, record actual
checkpoint/runtime/decoding identities, and reserve bounded batches before
dispatch. Missing retained evidence must not cause a repeated forward pass.
Keep measured timing/RSS separate from deterministic semantic evidence and state
the platform's memory-enforcement mode. Its constructed correlated tasks do not
establish generalization, optimization, or Qwen benchmark performance.

The [adaptive CPU search](examples/weight_evolution/cpu_adaptive/README.md)
uses separate contracts from the fixture adapters. Keep the proposal policy's
input limited to practice counts and pin evaluation seeds outside its control.
Reserve materializations, proposals, probes, and restoration before execution;
replay all retained evidence before dispatching new work. Test an earlier winner,
changed-feedback branching, veto, incomplete claims, and real Linux limit failures.
Constructed-task scores establish adaptive execution correctness only.

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
- `ts/`: published TypeScript package, Node CLI, MCP server, and bundled pi-tui terminal UI
- `scripts/`: repo maintenance and protocol generation helpers

## Development Notes

- The Python package name and CLI are `autocontext` / `autoctx`.
- Environment variables use the `AUTOCONTEXT_` prefix.
- Prefer targeted tests for touched modules before running full suites.
- Use parity-last changes: implement one runtime first unless cross-runtime parity is user-visible in the same release. Note deferred parity in the PR.
- Keep protocol changes in sync with `scripts/generate_protocol.py`.
- Keep repository-relative Markdown paths and heading anchors valid with
  `scripts/check_markdown_links.py`. The deterministic check skips external URL
  availability plus generated run/knowledge artifacts, test fixtures/goldens,
  dependencies, caches, and build outputs.
- Keep proposed context and harness artifacts out of live paths. Use immutable
  context bundles and matched confirmation for activation; a strategy gate is
  not evidence for a context edit that the strategy did not exercise.
- Avoid rewriting historical plan docs unless the change is user-facing or release-facing.
- Use `autoctx scenario create` and `autoctx serve mcp` in public examples; reserve
  legacy aliases for explicitly labeled compatibility documentation.

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
- Pi extension publishes through `.github/workflows/publish-pi-autocontext.yml`
  - tag trigger: `pi-v<version>`
  - manual trigger: `workflow_dispatch` from `main`
  - environment: `publish-pi-autocontext`

Release notes:

- Keep the GitHub environment branch/tag policy restricted to `main` and the matching tag namespace.
- Merge release preparation into protected `main` and wait for CI on that exact
  commit before creating a release tag. The release guard is loaded from `main`,
  verifies that the release commit belongs to its history, and requires a
  successful main-push CI run with `lint`, `test`, `smoke`,
  `dependency-security`, `ts-lint`, and `ts-test` from GitHub Actions.
- Release tags are immutable and may be created only by the configured release
  maintainers. A different configured reviewer must approve publication;
  publishing environments prohibit self-approval and administrator bypass.
- The trusted publisher registration in PyPI and npm must match the repo, workflow filename, and environment name exactly.
- No `NPM_TOKEN`, `NODE_AUTH_TOKEN`, or PyPI API token should be required for the publish jobs.
- After cutover, remove the old combined `.github/workflows/publish.yml` publisher registration from PyPI and npm.

## Live Service Verification

Live PrimeIntellect checks run through the manual `live-integration` workflow
from `main`. They do not run on pull requests or automatically after merges.
Review the dispatched commit and approve the `live-integration` environment
using a different configured reviewer from the person who started the run.

Before enabling live checks, configure `AUTOCONTEXT_PRIMEINTELLECT_API_KEY` and
`AUTOCONTEXT_ANTHROPIC_API_KEY` as secrets of that environment. Do not expose
these keys as repository-wide or inherited organization secrets. Missing keys
fail the manual run. The optional accelerator check uses the existing
`AUTOCONTEXT_PRIMEINTELLECT_LIVE_*` environment variables.

Dependency setup receives no service-key environment variables. Only the live
commands receive the keys, and `uv run --no-sync` avoids reinstalling packages
while they are present. This limits accidental exposure; all reviewed code in
the job is still trusted, because step boundaries do not isolate malicious
background processes.

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

### Dependabot Updates

Dependabot batches eligible version updates weekly into one PR per ecosystem
and directory: GitHub Actions, `ts/`, `pi/`, Python, and Docker. Each has a limit
of one open version-update PR, for five across the repository instead of 25
individual updates. Groups include major versions, so review breaking changes
and make any needed adaptations before merging. The Pi SDK packages move in the
same batch rather than separate, potentially incompatible upgrades.

Security updates have separate groups and are not capped by the version-update
limit or delayed by the version cooldown. Existing dependency pins, ignore
rules, release-age checks, and required CI still apply. A grouped PR must keep
the npm and Bun locks synchronized, and Python manifest updates must include
the matching uv lock and any derived container requirements. The Python entry
uses Dependabot's native `uv` updater so its PRs update `uv.lock` alongside
`pyproject.toml`.

`secure-exec` stays on its reviewed 0.1 API. Its 0.2 line removes `NodeRuntime`
and the driver factories used by the generated-scenario and RLM sandboxes;
upgrading it requires a dedicated migration that revalidates isolation,
resource limits and disposal. Dependabot may propose patch updates, while its
minor and major version updates are deferred for that migration.

Python also retains the supported Anthropic 0.x HTTPX transport and MCP 1.x
FastMCP APIs. Their next major versions require dedicated client/server
migrations. Monty 0.0.18 uses the supported constructor and result-dictionary
snapshot API. The optional-runtime CI job installs the real runtime and verifies
callback dispatch, persistent state and native execution deadlines. Later Monty
releases require a separate interpreter/pool API migration.

CUDA uses the maintained, bundled Accelerate checkpoint security build. Source
checkouts install it through uv; published-package users install the bundled
build before adding the CUDA extra. See the [CUDA installation and patch
maintenance guide](docs/security/accelerate-risk-assessment.md). Full audits
verify the bundle and query all advisories for its upstream base; no
vulnerability exception remains.

Prime Sandboxes stays on its compatible 0.2 SDK line because newer lines remove
the VM request field used by accelerator isolation checks. Migrate that request
contract before allowing routine minor or major updates.

The new policy takes effect after `.github/dependabot.yml` reaches `main`.
Retire superseded individual PRs after it is active, then let Dependabot create
the replacement batches on its next scheduled run. Closing an old PR does not
mean its dependency was updated or its vulnerability fixed.

### npm Release Age

CI installs npm dependencies with `npm ci`, which does not enforce the
`NPM_CONFIG_MIN_RELEASE_AGE` that `.github/workflows/ci.yml` sets (currently
seven days). The `npm-release-age` job therefore compares `ts/package-lock.json`
and `pi/package-lock.json` with the pull request's merge base, and fails if the
pull request newly locks a registry version younger than that. `autoctx`, which
this repository publishes, is exempt. The failure lists each version with its
publish time and the UTC time it clears the policy: re-run the job after the
last of those times. Packages locked from git, a file or another host have no
registry publish time, so the job lists them with a warning instead. Entries
without a `resolved` URL, which npm writes under `omit-lockfile-registry-resolved`,
come from the registry and are checked.

To check a branch before pushing:

```bash
cd autocontext
NPM_CONFIG_MIN_RELEASE_AGE=7 uv run python ../scripts/check_npm_release_age.py --base origin/main --head HEAD
```

With `min-release-age=7` in your npm config (npm 11.10 or newer), `npm install`
resolves only versions that pass.

For an urgent security fix that cannot wait, a maintainer can apply the
`npm-release-age-override` label. The job then still lists the young versions
and warns, but passes. It reads labels from the event that started the run, and
a re-run reuses that event, so after adding the label start a new run: push a
commit, close and reopen the pull request, or comment `@dependabot recreate` on
a Dependabot pull request. The label keeps applying to later runs on that pull
request, so check its warning again if the lockfile changes after it is added.
