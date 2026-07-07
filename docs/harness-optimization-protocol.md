# Harness-optimization protocol

autocontext optimizes the harness that runs a task, not just the strategy inside
it. A candidate is one proposed change to a mechanism (a prompt playbook, a
deterministic code path, a tool wrapper, a context policy, a judge policy, or a
mix) that targets a specific surface of the harness. Before a candidate is
evaluated, it emits a **CandidateEvidence** artifact: a structured record of the
hypothesis behind the change, the concrete changes it makes, the fix and
regression cases it expects to move, its cost expectation, and its cross-language
parity status.

The schema is the single source of truth. It lives at
`ts/src/harness-optimization/contract/json-schemas/candidate-evidence.schema.json`
and generates both packages' types: the TypeScript `CandidateEvidence` interface
(`ts/src/harness-optimization/contract/generated-types.ts`) and the Python
`CandidateEvidence` pydantic model
(`autocontext/src/autocontext/harness_optimization/contract/models.py`). A CI
drift gate rejects any hand-edit to the generated files, so the two languages can
never disagree on the contract.

## Canonical example

The record below is the shared fixture-of-record at
`fixtures/harness-optimization/candidate-evidence/valid-full.json`. Both packages
load and validate this same file in their test suites.

```json
{
  "schema_version": 1,
  "candidate_id": "cand-full-002",
  "parent_frontier_id": "frontier-001",
  "mechanism_name": "require-target-theorem-present",
  "mechanism_type": "mixed",
  "target_surface": "evaluator",
  "hypothesis": "Requiring the target theorem to be present catches false-pass truncations.",
  "changes": "Add a post-compile check that the named theorem exists in the output, plus a playbook note describing the failure mode.",
  "changed_artifacts": ["src/autocontext/loop/loop_driver.py", "knowledge/lean/playbook.md"],
  "fix_cases": ["divergence-empty-file", "truncated-claude-p-output"],
  "regression_cases": ["convective_eq_fderiv", "divergence-integrability"],
  "observed": "Dry-run over the divergence seed now fails the compile-only oracle as expected.",
  "validation_plan": "Re-run the divergence seed and confirm the empty-file case now fails, then replay the 60 banked sub-lemmas for regressions.",
  "cost_expectation": {
    "extra_tokens": 1200,
    "extra_calls": 1,
    "extra_seconds": 4.5
  },
  "leakage_scope": ["public-fixtures", "banked-sub-lemmas"],
  "parity": {
    "python": "implemented",
    "typescript": "implemented",
    "schema_hash": "9f8e7d6c5b4a"
  }
}
```

## Reading and validating the artifact

Both packages expose the same read/write/validate surface. Reading validates the
record against the schema and raises (Python) or throws (TypeScript) on an
invalid artifact.

Python:

```python
from autocontext.harness_optimization.evidence import (
    read_candidate_evidence,
    write_candidate_evidence,
)

evidence = read_candidate_evidence("candidate.json")  # raises on invalid input
print(evidence.candidate_id, evidence.mechanism_type)
write_candidate_evidence(evidence, "out/candidate.json")  # stable key order + trailing newline
```

TypeScript:

```ts
import {
  readCandidateEvidence,
  writeCandidateEvidence,
} from "autoctx/harness-optimization/evidence";

const evidence = readCandidateEvidence("candidate.json"); // throws on invalid input
console.log(evidence.candidate_id, evidence.mechanism_type);
writeCandidateEvidence(evidence, "out/candidate.json"); // 2-space indent + trailing newline
```

## The contract pattern

This is the foundation artifact. The later protocol artifacts follow the same
contract pattern (one canonical JSON Schema generating both packages' types, a
shared fixture set, and a thin read/write/validate surface):

- promotion score (AC-877)
- repair result (AC-878)
- integrity metadata (AC-879)
- mechanism archive (AC-880)
- calibration report (AC-881)

Live-loop persistence (writing CandidateEvidence during real generation runs) and
prompt injection (feeding the record into the proposer's context) are a tracked
follow-up and are deliberately not part of this foundation PR. The read/write/
validate API here is enough to persist and inspect an artifact from both sides.
