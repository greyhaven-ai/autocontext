"""Paired quality and lifecycle reporting; unknown costs remain unknown."""

from __future__ import annotations

import statistics

from autocontext.execution.executable_skills import ProfileV1, _json, verify_migration_output
from autocontext.harness.benchmark_stats import paired_interval, quantile, wilson_interval
from benchmarks.skill_reuse.contracts import ARMS

RESOURCES = ("seconds", "model_calls", "tokens", "total_cost_usd")


def score_case(case, result):
    correct = False
    if case.cohort == "supported" and result.status == "success" and result.output_json is not None:
        try:
            verify_migration_output(ProfileV1.model_validate(_json(case.input_json)), result.output_json)
            correct = True
        except ValueError:
            pass
    if case.cohort == "shifted":
        # A rejected wrong proposal is safe containment, not a correct abstention.
        correct = (result.status == "abstention" and result.output_json is None and bool(result.attempts)
                   and result.attempts[-1].reason == "model_abstained")
    skill = [a for a in result.attempts if a.route == "skill"]
    proposals = [a for a in skill if a.status in {"success", "verification_failure"}]
    return {"case_id": case.id, "group": case.group, "cohort": case.cohort, "input_json": case.input_json,
            "input_digest": case.input_digest, "correct": correct, "selected_route": result.selected_route,
            "status": result.status, "reason": result.reason, "output_json": result.output_json,
            "model_calls": result.model_calls, "tokens": result.accounted_tokens,
            "declared_model_cost_usd": result.accounted_model_cost_usd,
            "model_cost_complete": result.model_cost_complete,
            "skill_proposals": len(proposals), "verified_skill_proposals": sum(a.status == "success" for a in proposals),
            "skill_accepted": result.selected_route == "skill" and correct,
            "harmful_proposals_rejected": sum(a.reason in {"migration_postcondition_failed", "unsupported_input_proposal"}
                                               for a in result.attempts),
            "fallback": bool(_json(result.config_json).get("bundle_digest")) and result.model_calls > 0,
            "attempts": [a.model_dump(mode="json") for a in result.attempts], "trace_path": result.trace_path}


def known_sum(values):
    return None if any(v is None for v in values) else sum(values)


def mean(values):
    return statistics.mean(values) if values else None


def setup_costs(learning):
    return {arm: {metric: known_sum([getattr(r, metric) for r in learning.discovery if arm in r.arms])
                  for metric in RESOURCES} for arm in ARMS}


def summarize_group(rows):
    count = len(rows)
    successes = sum(r["correct"] for r in rows)
    proposals = sum(r["skill_proposals"] for r in rows)
    verified = sum(r["verified_skill_proposals"] for r in rows)
    return {"cases": count, "successes": successes, "success_rate": successes / count if count else None,
            "descriptive_binomial_95_interval": wilson_interval(successes, count),
            "model_calls": sum(r["model_calls"] for r in rows), "tokens": sum(r["tokens"] for r in rows),
            "declared_model_cost_usd": sum(r["declared_model_cost_usd"] for r in rows),
            "seconds": sum(r["seconds"] for r in rows),
            "p50_seconds": quantile([r["seconds"] for r in rows], .5),
            "p95_seconds": quantile([r["seconds"] for r in rows], .95),
            "skill_proposals": proposals, "skill_precision": verified / proposals if proposals else None,
            "skill_precision_binomial_95_interval": wilson_interval(verified, proposals),
            "skill_coverage": sum(r["skill_accepted"] for r in rows) / count if count else None,
            "fallback_frequency": sum(r["fallback"] for r in rows) / count if count else None,
            "harmful_proposals_rejected": sum(r["harmful_proposals_rejected"] for r in rows),
            "cpu_seconds": known_sum([r["cpu_seconds"] for r in rows]) if rows else None,
            "peak_memory_bytes": (max(r["peak_memory_bytes"] for r in rows)
                                  if rows and all(r["peak_memory_bytes"] is not None for r in rows) else None),
            "estimated_task_cost_usd": known_sum([r["estimated_task_cost_usd"] for r in rows]) if rows else None,
            "total_cost_usd": known_sum([r["total_cost_usd"] for r in rows]) if rows else None}


def comparisons(rows, protocol):
    result = {}
    for cohort in ("supported", "shifted"):
        for candidate, incumbent in (("textual", "baseline"), ("executable", "textual"),
                                     ("executable", "cheap_textual"), ("cheap_textual", "textual")):
            left = {r["case_id"]: r for r in rows if r["arm"] == candidate and r["cohort"] == cohort}
            right = {r["case_id"]: r for r in rows if r["arm"] == incumbent and r["cohort"] == cohort}
            paired = sorted(left.keys() & right.keys())
            groups = {}
            for key in paired:
                groups.setdefault(left[key]["group"], []).append(int(left[key]["correct"]) - int(right[key]["correct"]))
            differences = [statistics.mean(groups[key]) for key in sorted(groups)]
            interval = (paired_interval(differences, seed=protocol.bootstrap_seed, resamples=protocol.bootstrap_resamples)
                        if len(differences) >= 2 else None)
            result[f"{cohort}/{candidate}-vs-{incumbent}"] = {
                "paired_cases": len(paired), "sampling_groups": len(groups), "mean_group_difference": mean(differences),
                "regressions": sum(left[k]["correct"] < right[k]["correct"] for k in paired),
                "conditional_group_bootstrap_95_interval": interval}
    return result


def economics(rows, protocol, learning, *, unreconciled=()):
    setup = setup_costs(learning)
    affected = {record["arm"] for record in unreconciled}
    actual, projections, first_use, qualification = {}, {}, {}, {}
    for arm in ARMS:
        group = [r for r in rows if r["arm"] == arm]
        successes = sum(r["correct"] for r in group)
        actual[arm] = {metric: None for metric in RESOURCES}
        first_use[arm] = {}
        qualification[arm] = {}
        for metric in RESOURCES:
            total = None if arm in affected else known_sum([setup[arm][metric], *[r[metric] for r in group]])
            actual[arm][metric] = total / successes if total is not None and successes else None
            qualification[arm][metric] = total
            first_use[arm][metric] = known_sum([setup[arm][metric], group[0][metric]]) if group else None
        projections[arm] = []
        for shifted in protocol.shifted_frequencies:
            supported_rows = [r for r in group if r["cohort"] == "supported"]
            shifted_rows = [r for r in group if r["cohort"] == "shifted"]
            for horizon in protocol.repetition_horizons:
                record = {"tasks": horizon, "shifted_frequency": shifted}
                for metric in RESOURCES:
                    supported = known_sum([r[metric] for r in supported_rows]) if supported_rows else None
                    changed = known_sum([r[metric] for r in shifted_rows]) if shifted_rows else None
                    if supported is None or changed is None or qualification[arm][metric] is None:
                        record[metric] = None
                    else:
                        record[metric] = qualification[arm][metric] + horizon * (
                            (1 - shifted) * supported / len(supported_rows) + shifted * changed / len(shifted_rows))
                projections[arm].append(record)
    # Observed cumulative resource crossings retain paired run order and setup.
    crossing = {}
    for reference in ("textual", "cheap_textual"):
        right = {r["case_id"]: r for r in rows if r["arm"] == reference and r["cohort"] == "supported"}
        paired = [r for r in rows if r["arm"] == "executable" and r["case_id"] in right]
        crossing[reference] = {}
        for metric in RESOURCES:
            if affected & {"executable", reference}:
                crossing[reference][metric] = None
                continue
            left_total, right_total = setup["executable"][metric], setup[reference][metric]
            first = None
            for i, row in enumerate(paired, 1):
                left_total = known_sum([left_total, row[metric]])
                right_total = known_sum([right_total, right[row["case_id"]][metric]])
                if first is None and left_total is not None and right_total is not None and left_total < right_total:
                    first = i
            crossing[reference][metric] = first
    return {"one_time_costs": setup, "cost_accounting_incomplete_arms": sorted(affected),
            "qualification_costs_including_evaluation": qualification,
            "first_observed_task_with_setup": first_use,
            "actual_lifecycle_resources_per_success": actual, "projected_horizons": projections,
            "observed_first_resource_crossing_vs": crossing,
            "projection_scope": "Projection pays discovery, development and this arm's evaluation before new tasks. "
                                "Shared qualification overhead belongs in the explicit discovery ledger. "
                                "Observed crossings refer to the evaluation sequence, not post-qualification deployment. "
                                "Unreconciled dispatches make affected totals, projections and crossings unknown. "
                                "Frozen artifact with shifted-input fallback, no retuning. Horizons are projections, not runs. "
                                "Candidate child CPU/peak RSS exclude Docker daemon and model-provider resources. "
                                "Operator-priced local/Docker seconds and provider-reported spend are conditional "
                                "estimates, not audited invoices; incomplete discovery costs remain unknown."}


def build_report(rows, protocol, learning, *, evidence_kind, split, expected_cases, status, unreconciled=()):
    stats = {f"{cohort}/{arm}": summarize_group([r for r in rows if r["arm"] == arm and r["cohort"] == cohort])
             for cohort in ("supported", "shifted") for arm in ARMS}
    paired = comparisons(rows, protocol)
    for cohort in ("supported", "shifted"):
        for arm in ARMS:
            count = sum(r["arm"] == arm and r.get("cohort", cohort) == cohort for r in unreconciled)
            stat = stats[f"{cohort}/{arm}"]
            stat["unreconciled_attempts"] = count
            if count:
                for metric in (*RESOURCES, "declared_model_cost_usd", "estimated_task_cost_usd", "cpu_seconds",
                               "peak_memory_bytes"):
                    stat[metric] = None
    costs = economics(rows, protocol, learning, unreconciled=unreconciled)
    complete = status == "complete" and not unreconciled and len(rows) == expected_cases * len(ARMS)
    enough = all(stats[f"{cohort}/baseline"]["cases"] >= protocol.min_cases_per_cohort for cohort in ("supported", "shifted"))
    enough = enough and all(c["sampling_groups"] >= protocol.min_groups_per_cohort for c in paired.values())
    quality = complete and enough and all(
        stats[f"{cohort}/executable"]["success_rate"] >= protocol.quality_floor for cohort in ("supported", "shifted"))
    quality = quality and all(paired[f"{cohort}/executable-vs-{reference}"]["conditional_group_bootstrap_95_interval"][0]
                             >= -protocol.acceptable_regression
                             for cohort in ("supported", "shifted") for reference in ("textual", "cheap_textual"))
    quality = quality and stats["supported/executable"]["skill_coverage"] >= protocol.min_skill_coverage
    quality = quality and stats["supported/executable"]["skill_precision"] == 1
    horizon = protocol.economic_horizon
    frequency = protocol.economic_shifted_frequency
    projected = {arm: next((row["total_cost_usd"] for row in costs["projected_horizons"][arm]
                            if row["tasks"] == horizon and row["shifted_frequency"] == frequency), None)
                 for arm in ARMS}
    measured_skill = all(r["cpu_seconds"] is not None and r["peak_memory_bytes"] is not None
                         for r in rows if r["arm"] == "executable"
                         and any(a["route"] == "skill" and a["status"] != "skipped" for a in r["attempts"]))
    economics_known = measured_skill and all(value is not None for value in projected.values())
    savings = (all(projected["executable"] <= (1 - protocol.min_economic_savings) * projected[arm]
                   for arm in ("textual", "cheap_textual")) if economics_known else None)
    if evidence_kind == "fixture" or split != "heldout":
        decision = "infrastructure_only" if evidence_kind == "fixture" else "development_only"
    elif not complete or not enough:
        decision = "inconclusive_incomplete_evidence"
    elif not quality:
        decision = "no_go_quality_or_coverage"
    elif not economics_known:
        # Current route receipts cover declared model spend, not CPU/Docker or
        # all lifecycle costs. Passing quality alone cannot establish an economic go.
        decision = "inconclusive_total_lifecycle_cost_unmeasured"
    else:
        decision = "conditional_go_for_promotion_review" if savings else "no_go_economics"
    return {"schema_version": "ac1029.report.v1", "evidence_kind": evidence_kind, "split": split, "status": status,
            "decision": decision, "economic_test": {"horizon": horizon, "shifted_frequency": frequency,
                                                "min_savings": protocol.min_economic_savings,
                                                "projected_total_cost_usd": projected, "passes": savings},
            "production_activation_authorized": False, "complete_pairs": complete,
            "quality_and_coverage_checks_pass": bool(quality), "unreconciled_reservations": list(unreconciled),
            "stats": stats, "comparisons": paired, **costs,
            "limitations": "Synthetic task-family pilot; intervals are conditional on frozen artifacts and model draws. "
                           "Bootstrap sampling units are paired fixture groups. Binomial intervals and latency quantiles "
                           "are descriptive. Public fixtures are not an independent private generalization suite. "
                           "No caching, tool changes, learned judge, retuning or work-skipping ablations are introduced."}


def render_report(report):
    lines = ["# Schema-migration reuse study", "", f"Decision: **{report['decision']}**.", "",
             f"Evidence: {report['evidence_kind']}; split: {report['split']}; status: {report['status']}.", "",
             "| Cohort / arm | Correct / cases | Model calls | Tokens | p50 / p95 seconds | Estimated task USD |",
             "|---|---:|---:|---:|---:|---:|"]
    for name, row in report["stats"].items():
        latency = "unknown" if row["p50_seconds"] is None else f"{row['p50_seconds']:.4f} / {row['p95_seconds']:.4f}"
        calls = row["model_calls"] if row["model_calls"] is not None else "unknown"
        tokens = row["tokens"] if row["tokens"] is not None else "unknown"
        estimate = ("unknown" if row["estimated_task_cost_usd"] is None
                    else f"{row['estimated_task_cost_usd']:.6f}")
        lines.append(f"| {name} | {row['successes']} / {row['cases']} | {calls} | {tokens} | {latency} | {estimate} |")
    measured = report["stats"]["supported/executable"]
    lines.extend(["", f"Supported skill child CPU / peak RSS: {measured['cpu_seconds']} seconds / "
                  f"{measured['peak_memory_bytes']} bytes (null means unknown).", "",
                  f"Lifecycle dollar decision is conditional on the frozen pricing assumptions: "
                  f"{report['economic_test']}. Unknown inputs remain unknown.", "",
                  "Case counts and latency describe completed outcomes. Unreconciled dispatches are retained in the ledger; "
                  "affected cost totals, projections and resource crossings remain unknown.", "",
                  "[Machine-readable report](summary.json) includes paired differences, precision/coverage, fallback, "
                  "one-time costs, first-use costs, observed resource crossings and projected reuse horizons.", "",
                  report["limitations"], "", "This report does not authorize production promotion.", ""])
    return "\n".join(lines)
