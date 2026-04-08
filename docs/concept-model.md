# Canonical Concept Model

This document is the working source of truth for AC-429: aligning the product vocabulary across Python, TypeScript, CLI, MCP, API/TUI surfaces, docs, and storage.

It does two jobs:

1. Defines the canonical concepts we want users and operators to see.
2. Maps today's surfaces (`run`, `task`, `solve`, sandbox, replay, artifacts, playbooks) onto that model so we can normalize names without losing existing workflows.

## Why This Exists

The repo already has strong runtime primitives, but the vocabulary is not yet uniform:

- `Task` means at least three different things today: an agent-task spec, a queued evaluation job, and a generic prompt.
- `Scenario` is sometimes a simulation environment and sometimes the saved wrapper around an agent task.
- `Mission` exists as a real TypeScript control-plane concept, but not yet as a shared repo-wide one.
- `Campaign` now has partial TypeScript API/MCP support, but it is not yet a shared CLI workflow or Python package surface.
- `solve`, `sandbox`, `replay`, `playbook`, and `artifacts` are often presented like peer concepts even though they are better understood as operations or runtime outputs.

## Canonical Layers

### User-facing concepts

These are the nouns we should prefer in docs, APIs, and product copy when describing what the system helps a person do.

| Concept | Definition | Current status |
| --- | --- | --- |
| `Scenario` | A reusable environment, simulation, or evaluation context with stable rules and scoring. | Implemented across Python, TypeScript, CLI, MCP, API/TUI surfaces, and docs. |
| `Task` | A user-authored unit of work or prompt-centric objective that can be evaluated directly or embedded inside another surface. | Implemented, but overloaded. |
| `Mission` | A long-running goal advanced step by step until a verifier says it is complete. | Implemented in TypeScript CLI/MCP/API/TUI surfaces. |
| `Campaign` | A planned grouping of missions, runs, and/or scenarios used to coordinate broader work over time. | Partially implemented through TypeScript API/MCP surfaces. Not yet a shared CLI workflow or Python package surface. |

### Runtime concepts

These are the execution nouns we should use when describing how the system actually runs.

| Concept | Definition | Current status |
| --- | --- | --- |
| `Run` | A concrete execution instance of a `Scenario` or `Task`. | Implemented broadly. |
| `Step` | A bounded action taken while advancing a `Mission` or another long-running workflow. | Implemented for missions. |
| `Verifier` | The runtime check that decides whether a mission, step, or output is acceptable. | Implemented for missions and several evaluation flows. |
| `Artifact` | A persisted runtime output such as a replay, checkpoint, package, report, harness, or skill export. | Implemented broadly. |
| `Knowledge` | Persisted learned state that should carry forward across runs, such as playbooks, hints, lessons, and analysis. | Implemented broadly. |
| `Budget` | Constraints that bound runtime behavior, such as max steps, cost, time, or retries. | Implemented in several places, but not yet described consistently. |
| `Policy` | Structured rules that constrain or guide runtime behavior, such as escalation, hint volume, cost, conflict, or harness policies. | Implemented in pockets, but not yet presented as one concept. |

## Relationship Model

- A `Scenario` or `Task` can be executed as a `Run`.
- A `Mission` advances through `Step`s and relies on one or more `Verifier`s.
- A `Mission` may launch or inspect `Run`s, but it is not itself a `Run`.
- A `Campaign` groups related `Mission`s, `Run`s, and supporting context.
- `Run`s and `Mission`s emit `Artifact`s.
- Some `Artifact`s become durable `Knowledge` when they are validated and meant to persist.
- `Budget` and `Policy` shape how `Run`s and `Mission`s are allowed to proceed.

## Mapping Today's Surfaces

| Current surface | Canonical meaning | Notes |
| --- | --- | --- |
| `run` | `Run` operation over a `Scenario` or `Task` | Keep the verb for CLI/MCP, but document the noun as `Run`. |
| `task` queue / `TaskRow` | Background job runtime, not the user-facing `Task` concept | This is the sharpest naming collision today. |
| `AgentTask` / `AgentTaskSpec` | Current implementation of a prompt-centric `Task` | Valid internal name, but user docs should emphasize `Task`. |
| `solve` | Workflow that creates or selects a `Scenario`/`Task`, then launches a `Run` and exports resulting knowledge | `solve` is an operation, not a peer object model noun. |
| sandbox | Isolated execution boundary for a `Run` | Better treated as runtime isolation/policy, not a top-level concept. |
| replay | `Artifact` view over a `Run` generation | The replay itself is an artifact. |
| playbook | A `Knowledge` artifact | It should be described as one kind of knowledge, not the whole knowledge system. |
| artifacts | Umbrella category over runtime outputs | Use as a collection term, not a peer to `Scenario` or `Mission`. |

## Current Gaps And Risks

- `Campaign` now has a TypeScript storage model plus API/MCP surfaces, but it still lacks a shared CLI workflow and Python package support.
- Python and TypeScript both have strong `Scenario` and `Run` surfaces, but only TypeScript currently has a first-class `Mission` model.
- The TypeScript package exposes both `Scenario` execution and `Mission` control-plane features, while the Python package is still more `Scenario`/`Run`/`Knowledge` centric.
- Queueing and evaluation code use `Task` for runtime jobs, which collides with the intended user-facing `Task` concept.
- `Policy` exists in many specific forms, but not yet as a discoverable shared runtime concept.

## Recommended Implementation Phases

### Phase 1: Make the model explicit

- Keep this document as the canonical vocabulary guide.
- Link it from the repo, Python, and TypeScript entry-point docs.
- Treat `Campaign` as a partial TypeScript-only feature until it has a shared CLI workflow and Python package support.

### Phase 2: Add shared metadata to machine-readable surfaces

- Expose the concept model in capability-discovery outputs for CLI and MCP.
- Let API, TUI, and external-agent surfaces point back to the same canonical names.
- Prefer one shared metadata shape over hand-maintained prose in each surface.

### Phase 3: Normalize the highest-friction names

- Separate the runtime queue/job meaning of `Task` from the user-facing `Task` concept.
- Tighten when `Scenario` is used versus when `Task` is used for prompt-centric evaluation.
- Treat `solve`, `sandbox`, and `replay` as operations around canonical objects rather than as peer nouns.

### Phase 4: Add missing concepts deliberately

- Expand `Campaign` from the current TypeScript API/MCP implementation into shared CLI and Python surfaces once the ownership model is clear.
- Keep the relationship to `Mission` and `Run` explicit as campaign support expands beyond the current TypeScript control-plane surface.

## Naming Guidance

- Prefer `Scenario`, `Task`, `Mission`, and `Campaign` in user-facing docs and product copy.
- Prefer `Run`, `Step`, `Verifier`, `Artifact`, `Knowledge`, `Budget`, and `Policy` when describing execution behavior.
- Use `playbook`, `replay`, `checkpoint`, `package`, `skill`, and `sandbox` as specific artifacts or operations, not as top-level peer concepts.
- Where internal code still uses legacy names, document the mapping explicitly rather than pretending the mismatch does not exist.
