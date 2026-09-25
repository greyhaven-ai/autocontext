# Schema-migration reuse study

Decision: **development_only**.

Evidence: live; split: development; status: complete.

| Cohort / arm | Correct / cases | Model calls | Tokens | p50 / p95 seconds | Estimated task USD |
|---|---:|---:|---:|---:|---:|
| supported/baseline | 2 / 2 | 2 | 272 | 2.2773 / 2.5944 | unknown |
| supported/textual | 2 / 2 | 2 | 1388 | 3.0526 / 3.1227 | unknown |
| supported/executable | 2 / 2 | 0 | 0 | 0.3984 / 0.5382 | unknown |
| supported/cheap_textual | 0 / 2 | 2 | 1396 | 1.5628 / 1.7240 | unknown |
| shifted/baseline | 2 / 2 | 2 | 249 | 2.9601 / 3.1085 | unknown |
| shifted/textual | 2 / 2 | 2 | 1365 | 2.2072 / 2.7752 | unknown |
| shifted/executable | 2 / 2 | 2 | 1365 | 2.0572 / 4.1471 | unknown |
| shifted/cheap_textual | 1 / 2 | 2 | 1368 | 1.6291 / 1.6873 | unknown |

Supported skill child CPU / peak RSS: 0.0024419999999999997 seconds / 11747328 bytes (null means unknown).

Lifecycle dollar decision is conditional on the frozen pricing assumptions: {'horizon': 100, 'shifted_frequency': 0.1, 'min_savings': 0.05, 'projected_total_cost_usd': {'baseline': None, 'textual': None, 'executable': None, 'cheap_textual': None}, 'passes': None}. Unknown inputs remain unknown.

Case counts and latency describe completed outcomes. Unreconciled dispatches are retained in the ledger; affected cost totals, projections and resource crossings remain unknown.

[Machine-readable report](summary.json) includes paired differences, precision/coverage, fallback, one-time costs, first-use costs, observed resource crossings and projected reuse horizons.

Synthetic task-family pilot; intervals are conditional on frozen artifacts and model draws. Bootstrap sampling units are paired fixture groups. Binomial intervals and latency quantiles are descriptive. Public fixtures are not an independent private generalization suite. No caching, tool changes, learned judge, retuning or work-skipping ablations are introduced.

This report does not authorize production promotion.
