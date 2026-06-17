# Run Progress Report

Canonical JSON shape: [`run-progress-report.json`](run-progress-report.json).

The report turns tree-search or campaign events into operator-facing progress:

- `progress_points` — best score over wall-clock time, with the generation and hypothesis node that caused each score.
- `milestones` — time to first valid candidate, first passing verifier, first advancement, and threshold success.
- `pass_at_k` — observed pass@k / best-of-k summaries for configured k values.
- `branch_lineage` — parent/child hypothesis edges for inspection artifacts.

Python and TypeScript parity is exercised by [`run-progress-report-parity-fixture.json`](run-progress-report-parity-fixture.json).
