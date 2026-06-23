# RubricSpec contract

`RubricSpec` is Autocontext's shared typed rubric artifact for Python and TypeScript.

Source of truth:

- Schema: [`rubric-spec.json`](./rubric-spec.json)
- Parity fixtures: [`rubric-spec-parity-fixtures.json`](./rubric-spec-parity-fixtures.json)

## MVP fields

- `schema_version`: contract version, currently `1`.
- `rubric_id`: stable rubric identifier.
- `goal`: what the rubric judges.
- `criteria`: declared result dimensions. Judge outputs must use these criterion IDs.
- `scales`: named scoring scales; MVP kinds are `numeric` and `binary`.
- `scope`: included/excluded evidence boundaries.
- `corpus_profile`: optional domain/audience/source notes.
- `disqualifiers`, `evidence_requirements`, `output_constraints`, `decision_thresholds`: optional guardrails.

## Legacy string rubrics

Existing `--rubric` string callers remain valid. A string rubric is wrapped as one criterion:

```json
{
  "schema_version": 1,
  "rubric_id": "legacy-string-rubric",
  "goal": "<rubric text>",
  "criteria": [
    { "id": "overall", "description": "<rubric text>", "scale_id": "score", "weight": 1 }
  ],
  "scales": [{ "id": "score", "kind": "numeric", "min_score": 0, "max_score": 1 }]
}
```

## Parity surfaces

Python and TypeScript must preserve equivalent behavior for:

- CLI `judge` string and typed rubric inputs.
- improve/task queue judge evaluation payloads.
- MCP/API judge evaluation payloads.
- persisted score/result payloads, especially criterion-bound `dimension_scores` / `dimensionScores`.
- docs examples and parity fixtures.

## Reserved for later

Rubric evolution/calibration may propose typed patches to text/scope/anchor fields, but must not mutate criterion IDs, scale kinds/ranges, or result payload contracts without a new schema version.
