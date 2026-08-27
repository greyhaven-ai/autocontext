"""Schema-aware derivation of benchmark observation metrics."""

from __future__ import annotations

import statistics
from typing import Any

from autocontext.kernel_evolution.evaluator_config import KernelBenchmarkEvaluatorConfig
from autocontext.kernel_evolution.finite_sample import derive_finite_sample_receipt
from autocontext.kernel_evolution.models import KernelBenchmarkReport, kernel_benchmark_report_digest
from autocontext.kernel_evolution.promotion_statistics import bootstrap_lcb, geometric_mean_ratio, percentile
from autocontext.kernel_evolution.protocols import KernelMeasurementDesign


def derive_observation_metrics(
    report: KernelBenchmarkReport,
    config: KernelBenchmarkEvaluatorConfig,
) -> dict[str, Any]:
    """Recompute every derived metric under the configured schema family."""

    assert report.performance is not None
    assert report.correctness is not None
    blocks = report.performance.blocks
    candidate_times = [float(block.candidate_ms) for block in blocks]
    incumbent_times = [float(block.incumbent_ms) for block in blocks]
    reference_times = [float(block.reference_ms) for block in blocks]
    sequential = report.protocol.sequential_testing
    alpha = sequential.per_proposal_alpha if sequential is not None else 0.05
    confidence_level = 1.0 - alpha
    all_cases = all(case.passed_no_regression for case in report.performance.cases) if report.performance.cases else None

    if config.statistics_method == "paired-sign-eprocess/v1":
        if report.schema_version != "autocontext.kernelbench-eval/v4":
            raise ValueError("finite-sample evaluation requires an autocontext.kernelbench-eval/v4 report")
        design = KernelMeasurementDesign.model_validate(report.metadata.get("measurement_design"))
        policy = config.statistics_policy
        if (
            design.block_definition != policy.block_definition
            or design.dependence_assumption != policy.dependence_assumption
            or design.schedule_seed_derivation != policy.seed_derivation
        ):
            raise ValueError("measurement design disagrees with the finite-sample statistics policy")
        receipt = derive_finite_sample_receipt(
            blocks=[
                (float(block.candidate_ms), float(block.incumbent_ms), float(block.reference_ms)) for block in blocks
            ],
            statistics_policy=policy,
            raw_report_digest=kernel_benchmark_report_digest(report),
            schedule_seed_material=report.protocol.seed_commitment,
            per_look_alpha=alpha,
            all_case_no_regression_passed=all_cases,
        )
        feedback = (
            f"Correct on {report.correctness.tests_passed}/{report.correctness.tests_run} trials; "
            f"paired speedup {receipt.speedup_vs_incumbent:.4f}x vs incumbent; "
            f"finite-sample sign e-test wins={receipt.candidate_wins}/{receipt.sample_count}, "
            f"p<={receipt.p_value_bound:.8f} at alpha={alpha:.8f}."
        )
        return {
            "feedback": feedback,
            "derived_statistics_receipt": receipt,
            "candidate_median_ms": receipt.candidate_median_ms,
            "incumbent_median_ms": receipt.incumbent_median_ms,
            "reference_median_ms": receipt.reference_median_ms,
            "speedup_vs_incumbent": receipt.speedup_vs_incumbent,
            "speedup_vs_reference": receipt.speedup_vs_reference,
            "speedup_lcb95": None,
            "speedup_lcb": None,
            "confidence_level": confidence_level,
            "all_case_no_regression_passed": receipt.all_case_no_regression_passed,
            "relative_improvement": receipt.relative_improvement,
            "candidate_p95_ms": receipt.candidate_p95_ms,
            "incumbent_p95_ms": receipt.incumbent_p95_ms,
            "environment_drift_ratio": receipt.environment_drift_ratio,
        }

    if report.schema_version == "autocontext.kernelbench-eval/v4":
        raise ValueError("v4 reports require the finite-sample statistics policy")
    assert config.bootstrap_samples is not None
    candidate_median = statistics.median(candidate_times)
    incumbent_median = statistics.median(incumbent_times)
    reference_median = statistics.median(reference_times)
    speedup_incumbent = geometric_mean_ratio(incumbent_times, candidate_times)
    speedup_reference = geometric_mean_ratio(reference_times, candidate_times)
    seed_material = f"{report.baseline_id}:{report.hardware_scope_id}:{report.protocol.seed_commitment}"
    lcb95 = bootstrap_lcb(
        list(zip(candidate_times, incumbent_times, strict=True)),
        samples=config.bootstrap_samples,
        seed_material=seed_material,
        alpha=0.05,
    )
    lcb = bootstrap_lcb(
        list(zip(candidate_times, incumbent_times, strict=True)),
        samples=config.bootstrap_samples,
        seed_material=seed_material,
        alpha=alpha,
    )
    quartile = max(1, len(reference_times) // 4)
    drift = abs(statistics.median(reference_times[-quartile:]) / statistics.median(reference_times[:quartile]) - 1.0)
    feedback = (
        f"Correct on {report.correctness.tests_passed}/{report.correctness.tests_run} trials; "
        f"paired speedup {speedup_incumbent:.4f}x vs incumbent "
        f"({confidence_level:.2%} empirical bootstrap quantile {lcb:.4f}x), "
        f"{speedup_reference:.4f}x vs reference."
    )
    return {
        "feedback": feedback,
        "derived_statistics_receipt": None,
        "candidate_median_ms": candidate_median,
        "incumbent_median_ms": incumbent_median,
        "reference_median_ms": reference_median,
        "speedup_vs_incumbent": speedup_incumbent,
        "speedup_vs_reference": speedup_reference,
        "speedup_lcb95": lcb95,
        "speedup_lcb": lcb,
        "confidence_level": confidence_level,
        "all_case_no_regression_passed": all_cases,
        "relative_improvement": 1.0 - (1.0 / speedup_incumbent),
        "candidate_p95_ms": percentile(candidate_times, 0.95),
        "incumbent_p95_ms": percentile(incumbent_times, 0.95),
        "environment_drift_ratio": drift,
    }


__all__ = ["derive_observation_metrics"]
