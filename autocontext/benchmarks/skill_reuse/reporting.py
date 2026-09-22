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
            "peak_memory_bytes": None, "total_cost_usd": known_sum([r["total_cost_usd"] for r in rows]) if rows else None}


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


def economics(rows, protocol, learning):
    setup = setup_costs(learning)
    actual, projections, first_use, qualification = {}, {}, {}, {}
    for arm in ARMS:
        group = [r for r in rows if r["arm"] == arm]
        successes = sum(r["correct"] for r in group)
        actual[arm] = {metric: None for metric in RESOURCES}
        first_use[arm] = {}
        qualification[arm] = {}
        for metric in RESOURCES:
            total = known_sum([setup[arm][metric], *[r[metric] for r in group]])
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
            left_total, right_total = setup["executable"][metric], setup[reference][metric]
            first = None
            for i, row in enumerate(paired, 1):
                left_total = known_sum([left_total, row[metric]])
                right_total = known_sum([right_total, right[row["case_id"]][metric]])
                if first is None and left_total is not None and right_total is not None and left_total < right_total:
                    first = i
            crossing[reference][metric] = first
    return {"one_time_costs": setup, "qualification_costs_including_evaluation": qualification,
            "first_observed_task_with_setup": first_use,
            "actual_lifecycle_resources_per_success": actual, "projected_horizons": projections,
            "observed_first_resource_crossing_vs": crossing,
            "projection_scope": "Projection pays discovery, development and this arm's evaluation before new tasks. "
                                "Shared qualification overhead belongs in the explicit discovery ledger. "
                                "Observed crossings refer to the evaluation sequence, not post-qualification deployment. "
                                "Frozen artifact with shifted-input fallback, no retuning. Horizons are projections, not runs. "
                                "CPU/memory, Docker cost and remote invoices are not measured by this adapter. "
                                "Declared model prices are partial estimates, never total lifecycle dollars."}


def build_report(rows, protocol, learning, *, evidence_kind, split, expected_cases, status):
    stats = {f"{cohort}/{arm}": summarize_group([r for r in rows if r["arm"] == arm and r["cohort"] == cohort])
             for cohort in ("supported", "shifted") for arm in ARMS}
    paired = comparisons(rows, protocol)
    costs = economics(rows, protocol, learning)
    complete = status == "complete" and len(rows) == expected_cases * len(ARMS)
    enough = all(stats[f"{cohort}/baseline"]["cases"] >= protocol.min_cases_per_cohort for cohort in ("supported", "shifted"))
    enough = enough and all(c["sampling_groups"] >= protocol.min_groups_per_cohort for c in paired.values())
    quality = complete and enough and all(
        stats[f"{cohort}/executable"]["success_rate"] >= protocol.quality_floor for cohort in ("supported", "shifted"))
    quality = quality and all(paired[f"{cohort}/executable-vs-{reference}"]["conditional_group_bootstrap_95_interval"][0]
                             >= -protocol.acceptable_regression
                             for cohort in ("supported", "shifted") for reference in ("textual", "cheap_textual"))
    quality = quality and stats["supported/executable"]["skill_coverage"] >= protocol.min_skill_coverage
    quality = quality and stats["supported/executable"]["skill_precision"] == 1
    if evidence_kind == "fixture" or split != "heldout":
        decision = "infrastructure_only" if evidence_kind == "fixture" else "development_only"
    elif not complete or not enough:
        decision = "inconclusive_incomplete_evidence"
    elif not quality:
        decision = "no_go_quality_or_coverage"
    else:
        # Current route receipts cover declared model spend, not CPU/Docker or
        # all lifecycle costs. Passing quality alone cannot establish an economic go.
        decision = "inconclusive_total_lifecycle_cost_unmeasured"
    return {"schema_version": "ac1029.report.v1", "evidence_kind": evidence_kind, "split": split, "status": status,
            "decision": decision, "production_activation_authorized": False, "complete_pairs": complete,
            "quality_and_coverage_checks_pass": bool(quality), "stats": stats, "comparisons": paired, **costs,
            "limitations": "Synthetic task-family pilot; intervals are conditional on frozen artifacts and model draws. "
                           "Bootstrap sampling units are paired fixture groups. Binomial intervals and latency quantiles "
                           "are descriptive. Public fixtures are not an independent private generalization suite. "
                           "No caching, tool changes, learned judge, retuning or work-skipping ablations are introduced."}


def render_report(report):
    lines = ["# Schema-migration reuse study", "", f"Decision: **{report['decision']}**.", "",
             f"Evidence: {report['evidence_kind']}; split: {report['split']}; status: {report['status']}.", "",
             "| Cohort / arm | Correct / cases | Model calls | Tokens | p50 / p95 seconds |",
             "|---|---:|---:|---:|---:|"]
    for name, row in report["stats"].items():
        latency = "unknown" if row["p50_seconds"] is None else f"{row['p50_seconds']:.4f} / {row['p95_seconds']:.4f}"
        lines.append(f"| {name} | {row['successes']} / {row['cases']} | {row['model_calls']} | {row['tokens']} | {latency} |")
    lines.extend(["", "Total lifecycle dollars, CPU and peak memory: **unknown** with the current adapter.", "",
                  "[Machine-readable report](summary.json) includes paired differences, precision/coverage, fallback, "
                  "one-time costs, first-use costs, observed resource crossings and projected reuse horizons.", "",
                  report["limitations"], "", "This report does not authorize production promotion.", ""])
    return "\n".join(lines)
