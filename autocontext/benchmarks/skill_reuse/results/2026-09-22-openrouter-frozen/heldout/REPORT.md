# Schema-migration reuse study

Decision: **inconclusive_total_lifecycle_cost_unmeasured**.

Evidence: live; split: heldout; status: complete.

| Cohort / arm | Correct / cases | Model calls | Tokens | p50 / p95 seconds | Estimated task USD |
|---|---:|---:|---:|---:|---:|
| supported/baseline | 12 / 12 | 12 | 1682 | 2.4307 / 5.1095 | unknown |
| supported/textual | 12 / 12 | 12 | 8378 | 2.5041 / 9.7435 | unknown |
| supported/executable | 12 / 12 | 0 | 0 | 0.3897 / 1.3859 | unknown |
| supported/cheap_textual | 0 / 12 | 12 | 8426 | 1.8274 / 2.3124 | unknown |
| shifted/baseline | 10 / 12 | 12 | 1508 | 2.1852 / 2.9217 | unknown |
| shifted/textual | 12 / 12 | 12 | 8175 | 2.1006 / 3.0245 | unknown |
| shifted/executable | 12 / 12 | 12 | 8175 | 2.1628 / 2.8394 | unknown |
| shifted/cheap_textual | 3 / 12 | 12 | 8221 | 1.6697 / 2.6087 | unknown |

Supported skill child CPU / peak RSS: 0.040719 seconds / 11804672 bytes (null means unknown).

Lifecycle dollar decision is conditional on the frozen pricing assumptions: {'horizon': 100, 'shifted_frequency': 0.1, 'min_savings': 0.05, 'projected_total_cost_usd': {'baseline': None, 'textual': None, 'executable': None, 'cheap_textual': None}, 'passes': None}. Unknown inputs remain unknown.

Case counts and latency describe completed outcomes. Unreconciled dispatches are retained in the ledger; affected cost totals, projections and resource crossings remain unknown.

[Machine-readable report](summary.json) includes paired differences, precision/coverage, fallback, one-time costs, first-use costs, observed resource crossings and projected reuse horizons.

Synthetic task-family pilot; intervals are conditional on frozen artifacts and model draws. Bootstrap sampling units are paired fixture groups. Binomial intervals and latency quantiles are descriptive. Public fixtures are not an independent private generalization suite. No caching, tool changes, learned judge, retuning or work-skipping ablations are introduced.

This report does not authorize production promotion.

## Post-run review (model credits only)

All 24 paired synthetic held-out cases and 96 arm/case evaluations completed. The executable and matched textual arms each served 24/24; their paired correctness difference is 0 in both cohorts (conditional four-group bootstrap interval [0, 0]). The executable arm accepted 12/12 supported proposals with no runtime model calls and abstained to the matched textual model on all 12 shifted cases. The baseline served 22/24; the cheaper-model textual control served 3/24 (0/12 supported). Thus this pilot shows no quality improvement over matched textual context, and the cheaper control is not a quality-preserving substitute at these settings. These are conditional observations from one candidate and one draw per case, not independent or representative task-family generalization.

The following amounts are OpenRouter-reported model-credit charges in USD-denominated credits, **not** total lifecycle dollars. Development includes its four cases. One-time learning allocates the initial failed playbook call ($0.001326) and successful playbook call ($0.009408) fully to each consumer, plus the successful skill call ($0.004743) to the executable arm. Shared learning is charged fully to each consuming arm for independent-deployment comparisons; do not sum the per-arm totals as actual account spend.

| Arm | Learning credits | Development credits | Held-out credits | Arm-accounted credits |
|---|---:|---:|---:|---:|
| Baseline | $0 | $0.002295 | $0.014610 | $0.016905 |
| Textual | $0.010734 | $0.008991 | $0.054351 | $0.074076 |
| Executable | $0.015477 | $0.004311 | $0.025821 | $0.045609 |
| Cheap textual | $0.010734 | $0.003068 | $0.018683 | $0.032485 |

Actual account charges for the three synthesis calls, all development calls and all held-out calls sum to **$0.147607** in reported model credits. The separate frozen plan reserved no more than $0.15 for synthesis and $4 for final evaluation; observed charges were below those caps. Credit purchase fees, authoring time, Docker/host allocation and idle capacity, remote provider resource use, and periodic validation/retuning are not measured. The candidate-process measurement on supported cases was 0.040719 CPU seconds in total with a maximum observed child RSS of 11,804,672 bytes; this is not whole-container or cloud memory. No total cost per successful task or economic break-even is established.

**Decision:** quality/coverage screens passed for this narrowly defined synthetic transformation, but there is no demonstrated quality gain over the textual control and no measurable total-lifecycle economic go. Keep the candidate inactive. A future independent corpus, measured local and lifecycle cost allocations, and AC-1021's separate promotion gates are necessary before any production consideration.
