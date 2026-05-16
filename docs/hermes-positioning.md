# Hermes Curator + autocontext: Positioning

Short doc to keep the product story crisp when autocontext is used
alongside [Hermes v0.12](https://github.com/NousResearch/hermes-agent)
and its Curator subsystem.

The headline:

> **Hermes Curator is the live skill-library maintainer.
> autocontext is the evaluation, trace, replay, export, and
> local-training layer.**

They are complementary. autocontext does **not** replace Curator: it
observes Curator's outputs, evaluates them, and turns them into
durable artifacts (traces, datasets, exports) that operators and
training pipelines can consume.

## At a glance

| Concern                                   | Hermes Curator       | autocontext                                                                                 |
| ----------------------------------------- | -------------------- | ------------------------------------------------------------------------------------------- |
| Live skill mutation (`~/.hermes/skills/`) | **Yes** (sole owner) | No (read-only inspection only)                                                              |
| Curator decision logs                     | Source of truth      | Ingest target                                                                               |
| Session and trajectory data               | Hermes writes        | autocontext imports (with explicit redaction)                                               |
| Evaluation against a rubric               | Out of scope         | `autoctx judge` / `autoctx improve`                                                         |
| Replay / artifact storage                 | Per-Hermes-run logs  | Durable `Run` / `Artifact` / `Knowledge` model (see [concept-model.md](./concept-model.md)) |
| Local MLX / CUDA training                 | Out of scope         | `autoctx train` (advisory, narrow)                                                          |
| Exporting reusable skills                 | Out of scope         | `autoctx hermes export-skill`                                                               |

## Default operator flow

1. **Inspect Hermes** (read-only) to see what skills, usage telemetry,
   and Curator reports are available:

   ```bash
   autoctx hermes inspect --json
   autoctx hermes inspect --home "$HERMES_HOME" --json
   ```

   Detailed flag and output reference: see
   [agent-integration.md → autoctx hermes](../autocontext/docs/agent-integration.md#autoctx-hermes-inspect-hermes-and-export-the-hermes-skill).

2. **Install the autocontext skill into Hermes** so Hermes agents know
   when to use autocontext at all:

   ```bash
   autoctx hermes export-skill --output ~/.hermes/skills/autocontext/SKILL.md --json
   ```

3. **Evaluate** an agent or output via the autocontext CLI from inside
   a Hermes terminal session:

   ```bash
   autoctx judge -p "$PROMPT" -o "$OUTPUT" -r "$RUBRIC" --json
   autoctx improve --scenario my_saved_task -o "$OUTPUT" --json
   ```

4. **Inspect runs** and persisted knowledge:

   ```bash
   autoctx list
   autoctx show <run-id>
   autoctx replay <run-id> --generation 1
   ```

## Integration surfaces

The Hermes skill spells out CLI-first / MCP-optional ordering and is
the source of truth for agent-facing usage. See:

- The agent-rendered SKILL.md text:
  `autocontext/src/autocontext/hermes/skill.py` (the
  `render_autocontext_skill()` output is what `autoctx hermes
export-skill` writes).
- The shared agent-integration guide:
  [agent-integration.md](../autocontext/docs/agent-integration.md).

In short: an agent picks the simplest surface available. CLI first
(observable, easy to debug). MCP only if it's already configured.
Native Hermes runtime / plugin emitter / OpenAI-compatible gateway
are later capability paths.

## Read-only import boundary

`autoctx hermes inspect` does **not** mutate `~/.hermes`. It only
reads. The Curator artifacts autocontext can import are:

- **Curator decision reports** (per-run JSON + Markdown reports).
  Become autocontext `ProductionTrace` JSONL via `autoctx hermes
ingest-curator` (AC-704), and supervised training JSONL via
  `autoctx hermes export-dataset --kind curator-decisions` (AC-705).
  Both commands read only; pinned, bundled, and hub-installed skills
  are protected from becoming mutation targets in the dataset.

  ```bash
  autoctx hermes ingest-curator \
      --home ~/.hermes \
      --output traces/hermes-curator.jsonl \
      [--since 2026-05-01T00:00:00Z] \
      [--limit 100] \
      [--json]
  ```

  Privacy defaults: `--include-llm-final` and `--include-tool-args`
  are off; pass them explicitly to attach the curator's LLM final
  summary or raw tool args. The JSON summary (under `--json`) reports
  `runs_read`, `traces_written`, `skipped`, and per-run `warnings`.

- **Usage telemetry** (`~/.hermes/skills/.usage.json` and adjacent
  state). Used as context for joining decisions to skill use.
- **Session DB and trajectory samples** (`~/.hermes/state.db`,
  `trajectory_samples.jsonl`, `failed_trajectories.jsonl`). Imported
  only when explicitly requested and with redaction.

Curator stays the only writer to `~/.hermes/skills/`. autocontext
exports skills in the opposite direction: `autoctx hermes
export-skill` writes the autocontext skill into `~/.hermes/skills/`
so Hermes can load it. The export is one file, on one explicit
operator command.

## Privacy posture for session/trajectory imports

Sessions and trajectories contain raw model prompts and responses,
which can include sensitive content the operator did not intend for
external storage. Before any session or trajectory import:

- autocontext requires an explicit `--include-sessions` /
  `--include-trajectories` flag (no implicit inclusion).
- Imports run a redaction policy before persisting; the policy is
  shared with the production-traces redaction path
  ([see redaction module docs](../autocontext/docs/sandbox.md) for
  the runtime redaction surface).
- Imported batches are stored under
  `.autocontext/production-traces/ingested/<date>/*.jsonl` exactly
  like other production traces, so the same `autoctx
production-traces` lifecycle (rotate-salt, prune, policy) applies.

The autocontext side does not transmit imported content anywhere.
Outbound moves (e.g., dataset export for training) are separate
operator commands with their own consent surfaces.

## Local MLX / CUDA training

`autoctx train` produces narrow advisor models from local datasets
(see [agent-integration.md](../autocontext/docs/agent-integration.md)
for the command flags). The training path is intentionally scoped:

- Datasets are derived from curator decisions, traces, and rubric
  outcomes that the operator already has on disk.
- Training is for **narrow advisor classifiers** (e.g., should this
  skill be kept active, archived, or merged) rather than full agent
  replacement.
- **Small user datasets will not produce a frontier-quality model.**
  Use the advisor model to surface recommendations against the
  operator's actual workflow, not to claim benchmark performance
  improvements.

The advisor output is exposed as **read-only recommendations** to
Hermes Curator: Curator still owns the mutation. (See AC-708 / AC-709
for the in-flight training and recommendation surface; not yet
shipped.)

## Why autocontext does not replace Curator

Curator's job is to keep the live skill library coherent: prune stale
skills, consolidate near-duplicates, gate patches against test runs.
That work is **stateful and online** by design.

autocontext's job is to make Curator's work **auditable and
reusable**: every decision becomes an artifact, every artifact can be
replayed and exported, and the export shape is stable enough that
future agents (and human reviewers) can use it as evidence without
re-running the original session.

If autocontext started mutating `~/.hermes/skills/`, both systems
would have to coordinate every change. Keeping autocontext read-only
on Hermes state preserves the property that "running autocontext
against my Hermes home does not change what Hermes will do next."
That property is load-bearing for evaluation, replay, and review.

## Status (as of today)

- Shipped: `autoctx hermes inspect`, `autoctx hermes export-skill`,
  `autoctx hermes ingest-curator` (AC-704), `autoctx hermes
export-dataset --kind curator-decisions` (AC-705), `autoctx hermes
ingest-trajectories --redact standard|strict|off` (AC-706 slice 1),
  `autoctx hermes ingest-sessions --redact standard|strict|off`
  (AC-706 slice 2, read-only SQLite + schema drift + WAL/SHM
  independence), `autoctx hermes train-advisor --baseline` (AC-708
  slice 1, data + evaluation contract with majority-class baseline
  and insufficient-data floor), the rendered Hermes-format
  SKILL.md, the integration surface order decision (CLI-first /
  MCP-optional / native runtime / plugin / gateway).
- In flight: AC-708 slice 2 (logistic-regression / MLX / CUDA
  trained advisor), AC-709 (read-only recommendation surface),
  AC-707 (Hermes plugin emitter spike), AC-711 (skill validation),
  AC-712 (distribution).
- Out of scope (today): autocontext writing to `~/.hermes/skills/`,
  autocontext replacing Curator's pruning / consolidation /
  gating workflow, frontier-scale training from a single operator's
  Hermes home.
