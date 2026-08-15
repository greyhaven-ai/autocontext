# Scenario Parity Matrix — Python & TypeScript

> Current through PyPI `autocontext==0.16.1` and npm `autoctx@0.16.1`. This
> replaces the original AC-431 planning snapshot, which predated TypeScript
> materialization, family-aware solve routing, code generation, spec auto-heal,
> and operator-loop execution.

## Product Goal

A user can describe a scenario, task, mission, or related objective in plain
language, and the agent can build, persist, execute, evaluate, and adapt the
runtime structures needed to improve the result. Built-in scenarios are
deterministic fixtures for development and CI, not the product abstraction.

## Built-in Deterministic Fixtures

| Fixture | Python | TypeScript | Contract |
| --- | :---: | :---: | --- |
| `grid_ctf` | ✅ | ✅ | Game fixture |
| `othello` | ✅ | ✅ | Game fixture |
| `resource_trader` | — | ✅ | TypeScript game fixture |
| `word_count` | — | ✅ | TypeScript deterministic agent-task fixture |

TypeScript keeps game fixtures in `SCENARIO_REGISTRY` and deterministic agent
tasks in `AGENT_TASK_REGISTRY`. Python uses one scenario registry for its
built-ins and persisted custom scenarios.

## Shared Family Vocabulary

Both packages define the same 11 families and scenario markers.

| Family | Evaluation model | Primary output |
| --- | --- | --- |
| `game` | Tournament | JSON strategy |
| `agent_task` | Judge or deterministic evaluator | Free text, code, or schema-shaped JSON |
| `simulation` | Trace evaluation | Action trace |
| `artifact_editing` | Artifact validation | Artifact diff |
| `investigation` | Evidence evaluation | Action trace |
| `workflow` | Workflow evaluation | Action trace |
| `negotiation` | Negotiation evaluation | Action trace |
| `schema_evolution` | Schema adaptation | Action trace |
| `tool_fragility` | Drift adaptation | Action trace |
| `operator_loop` | Judgment evaluation | Action trace |
| `coordination` | Coordination evaluation | Action trace |

Python expresses these as typed interfaces and family pipelines. TypeScript
uses TypeScript contracts, Zod schemas, family designers, type guards, and a
sandboxed generated-scenario runtime.

## Public Creation Paths

The canonical command is `autoctx scenario create`; `autoctx new-scenario`
remains a compatibility alias.

| Path | Python 0.16.1 | TypeScript 0.16.1 |
| --- | --- | --- |
| Template | `scenario create --template <t> --name <n>` | Same; persists the selected template |
| Natural language | `scenario create --family <family> --name <n> --description "..."` for the nine registered custom family pipelines | `scenario create --description "..."`; classifies, designs, heals, validates, and materializes |
| Existing spec | Custom scenarios are loaded from `knowledge/_custom_scenarios/`; use the family or template path to scaffold | `scenario create --from-spec <file>` or `--from-stdin`; validates and materializes |
| Solve on demand | `autoctx solve "..."`; classifies and creates through the selected family path | `autoctx solve "..."`; classifies, heals, materializes, and selects the family execution route |
| MCP | `autocontext_solve_scenario` uses the Python solve manager | The MCP/server solve surface uses the TypeScript solve manager |

The Python family-specific `scenario create` registry currently covers
`simulation`, `artifact_editing`, `investigation`, `workflow`, `negotiation`,
`schema_evolution`, `tool_fragility`, `operator_loop`, and `coordination`.
Agent-task scaffolding is available through the three templates, while
plain-language `autoctx solve` can select `agent_task` directly.

## Materialization And Execution

| Family group | Python 0.16.1 | TypeScript 0.16.1 | Remaining difference |
| --- | --- | --- | --- |
| Built-in `game` | `GenerationRunner` tournament and Elo loop | `GenerationRunner` tournament and Elo loop | TypeScript has two additional fixtures |
| Generated `game` from `solve` | Generates, validates, registers, and runs the custom game | Persists the spec but rejects names absent from the built-in game registry | Custom generated-game execution is not symmetric |
| `agent_task` | Persists executable Python and runs the task-like improvement loop | Persists a runnable spec and runs `ImprovementLoop` | Implementation and persistence formats differ |
| `artifact_editing` | Generates executable Python and adapts it to the task-like improvement loop | Generates and validates JavaScript, then performs bounded artifact execution | TypeScript does not run the same iterative loop |
| `simulation`, `investigation`, `workflow`, `negotiation`, `schema_evolution`, `tool_fragility`, `coordination` | Family codegen plus `GenerationRunner` | Family codegen, execution validation, sandboxed action execution, and result packaging | TypeScript executes one bounded generated-scenario pass rather than Python's generation loop |
| `operator_loop` | Family codegen plus operator-loop execution | Family codegen, execution validation, clarification/escalation behavior, and bounded execution | Runtime implementations differ, but neither side is scaffolding-only |

All ten non-game TypeScript families now have durable materialization. The
nine codegen families emit `scenario.js`; `agent_task` persists a runnable
spec. Generated source is execution-validated before it is accepted. This
means the earlier AC-433 and AC-434 statements that TypeScript was spec-only or
game-only are no longer current.

## Current Intentional Differences

- Python and TypeScript share command paths and family names, but some
  `scenario create` flags are runtime-specific. Use `autoctx scenario create
  --help` in the selected runtime or inspect the [CLI contract](../cli-contract.md).
- Python owns the full generation-based control plane. TypeScript owns the TUI
  and several operator-facing/runtime adapter surfaces, and uses a bounded
  sandboxed executor for generated non-task families.
- TypeScript has spec auto-heal and execution validation for generated family
  source. Python performs validation inside its family creator and pipeline
  implementations.
- Template assets exist in both packages, but their on-disk formats differ
  (YAML/example bundles in Python and packaged JSON definitions in TypeScript).
- `resource_trader` and `word_count` remain TypeScript-only deterministic
  fixtures; they do not limit custom family creation in Python.

## Implementation References

- Python solve routing: `autocontext/src/autocontext/knowledge/solver.py`
- Python family creator registry:
  `autocontext/src/autocontext/scenarios/custom/creator_registry.py`
- TypeScript solve routing: `ts/src/knowledge/solve-scenario-routing.ts`
- TypeScript materialization: `ts/src/scenarios/materialize.ts`
- TypeScript codegen registry: `ts/src/scenarios/codegen/registry.ts`
- Cross-runtime CLI shapes: `docs/cli-contract.json`

---

*Last verified against the 0.16.1 release sources: 2026-08-15.*
