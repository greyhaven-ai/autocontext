# Run Utilization Report

AC-822 adds a descriptive report for parallel autoresearch runs. It does not gate runs.

## Window semantics

`window.started_at` and `window.completed_at` are the earliest and latest known telemetry timestamps from run events and role usage rows. `duration_seconds` is null when no telemetry has timestamps.

## Metrics

- `mean_runner_utilization`: `active_runner_seconds / runner_capacity_seconds`.
- `runner_capacity_seconds`: `window.duration_seconds * max_parallel_branches`.
- `mean_token_utilization`: `model_active_seconds / runner_capacity_seconds`.
- `token_throughput_per_second`: `total_tokens / model_active_seconds`.
- `tokens_to_success`: tokens from role usage rows at or before the first success event.
- `verifier_idle_seconds`: `runner_capacity_seconds - verifier_active_seconds`.
- `eval_throughput_per_second`: completed evaluations per window second.

Unknown telemetry is represented as `null`; token counts degrade to `0`.

Shared schema: [`run-utilization-report.json`](run-utilization-report.json). Parity fixture: [`run-utilization-report-parity-fixture.json`](run-utilization-report-parity-fixture.json).
