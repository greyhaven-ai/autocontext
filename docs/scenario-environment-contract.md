# Scenario Environment Contract

AC-820 promotes reset and verification from optional harness details to the public scenario boundary. The contract is ENPIRE-inspired but robotics-free: a serious Autocontext scenario should say how it resets, runs a candidate, verifies the result, scores it, preserves replay/evidence, and cleans up.

Canonical JSON shape: [`scenario-environment-contract.json`](scenario-environment-contract.json).

## Required lifecycle hooks

| Hook | Autocontext meaning |
| --- | --- |
| `setup` | Create or load the deterministic scenario environment. |
| `reset` | Return to a clean seeded state before each attempt. |
| `rollout` | Run the candidate strategy/output/action trace. |
| `verification` | Check validity with validators, probes, rubrics, or guardrails. |
| `scoring` | Emit the scalar score used by search and reports. |
| `replay` | Preserve enough timeline/transcript to replay the attempt. |
| `evidence` | Attach score explanations, metrics, validation errors, or judge feedback. |
| `cleanup` | Remove or declare any mutable resources after the attempt. |

Contract probes map into the `verification` hook. If a scenario synthesizes a terminal, directory, artifact, service, cleanup, media, or distributed probe suite, its hook should list that suite under `evidence_refs` and emit `validation_errors` or equivalent failures.

## Minimal example

```json
{
  "schema_version": 1,
  "scenario_name": "grid_ctf",
  "scenario_family": "game",
  "hooks": {
    "setup": [{ "kind": "setup", "label": "seeded initial state", "description": "initial_state(seed)", "required": true, "inputs": ["seed"], "emits": ["state"], "evidence_refs": [] }],
    "reset": [{ "kind": "reset", "label": "repeatable reset", "description": "initial_state(seed) again", "required": true, "inputs": ["seed"], "emits": ["state"], "evidence_refs": [] }],
    "rollout": [{ "kind": "rollout", "label": "strategy rollout", "description": "validate_actions + step", "required": true, "inputs": ["state", "strategy"], "emits": ["next_state"], "evidence_refs": [] }],
    "verification": [{ "kind": "verification", "label": "action checks", "description": "validation errors fail the run", "required": true, "inputs": ["state", "strategy"], "emits": ["validation_errors"], "evidence_refs": ["Result.validation_errors"] }],
    "scoring": [{ "kind": "scoring", "label": "scalar score", "description": "get_result().score", "required": true, "inputs": ["terminal_state"], "emits": ["scalar_score"], "evidence_refs": [] }],
    "replay": [{ "kind": "replay", "label": "timeline", "description": "Result.replay", "required": true, "inputs": ["result.replay"], "emits": ["replay_timeline"], "evidence_refs": [] }],
    "evidence": [{ "kind": "evidence", "label": "metrics", "description": "summary, metrics, validation errors", "required": true, "inputs": ["result"], "emits": ["summary", "metrics"], "evidence_refs": [] }],
    "cleanup": [{ "kind": "cleanup", "label": "stateless cleanup", "description": "no external resources", "required": true, "inputs": [], "emits": ["no_external_state"], "evidence_refs": [] }]
  }
}
```

Python and TypeScript mirror this durable wire shape; runtime internals may differ, but persisted contracts must keep these keys and hook names stable.
