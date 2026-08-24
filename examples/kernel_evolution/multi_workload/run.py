"""Run three bounded kernel families through one evolution/study contract.

From ``autocontext/``:

    uv run --frozen python ../examples/kernel_evolution/multi_workload/run.py

The adapter is deterministic and synthetic.  The generated report proves
orchestration and evidence invariants; it is not accelerator performance data.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from contract import (
    baseline_source,
    candidate_source,
    digest,
    load_manifest,
    problem_payload,
    protocol_payload,
    strategy_tags,
    workload_family_id,
)

from autocontext.kernel_evolution import (
    ExternalKernelBenchmarkRunner,
    KernelBenchmarkEvaluator,
    KernelBenchmarkEvaluatorConfig,
    KernelBenchmarkObservation,
    KernelBenchmarkProtocol,
    KernelCandidate,
    KernelEvolutionConfig,
    KernelEvolutionRunner,
    KernelGenerationBudget,
    KernelTransferEvidence,
    KernelWorkloadBudget,
    KernelWorkloadRunEvidence,
    KernelWorkloadSpec,
    build_kernel_transfer_evidence,
    build_kernel_workload_run_evidence,
    build_kernel_workload_study_report,
    read_kernel_campaign_status,
)

EXAMPLE_DIR = Path(__file__).resolve().parent
MANIFEST = EXAMPLE_DIR / "manifest.json"
ADAPTER = EXAMPLE_DIR / "adapter.py"
CONTRACT = EXAMPLE_DIR / "contract.py"


def _write_exact_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise RuntimeError(f"immutable study contract changed: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)


def _make_evaluator(problem: Path, *, problem_id: str) -> KernelBenchmarkEvaluator:
    runner = ExternalKernelBenchmarkRunner(
        [
            sys.executable,
            str(ADAPTER),
            "--candidate",
            "{candidate}",
            "--incumbent",
            "{incumbent}",
            "--artifact-identity-version",
            "{artifact_identity_version}",
            "--candidate-artifact-digest",
            "{candidate_artifact_digest}",
            "--incumbent-artifact-digest",
            "{incumbent_artifact_digest}",
            "--candidate-source-digest",
            "{candidate_source_digest}",
            "--incumbent-source-digest",
            "{incumbent_source_digest}",
            "--candidate-source-suffix",
            "{candidate_source_suffix}",
            "--incumbent-source-suffix",
            "{incumbent_source_suffix}",
            "--candidate-entrypoint",
            "{candidate_entrypoint}",
            "--incumbent-entrypoint",
            "{incumbent_entrypoint}",
            "--report",
            "{report}",
            "--problem",
            str(problem),
        ],
        trusted_unsafe=True,
        immutable_paths=(ADAPTER, CONTRACT, problem),
    )
    return KernelBenchmarkEvaluator(
        runner,
        KernelBenchmarkEvaluatorConfig(
            problem_id=problem_id,
            min_timing_blocks=10,
            bootstrap_samples=2_000,
        ),
    )


def _evaluate_final(
    evaluator: KernelBenchmarkEvaluator,
    *,
    candidate: KernelCandidate,
    baseline: KernelCandidate,
) -> KernelBenchmarkObservation:
    scope = evaluator.evaluate(baseline, baseline)
    if not scope.eligible:
        raise RuntimeError(f"pinned workload baseline is not eligible: {scope.feedback}")
    return evaluator.evaluate(
        candidate,
        baseline,
        expected_scope_id=scope.hardware_scope_id,
        expected_baseline_id=scope.baseline_id,
        expected_protocol_id=scope.protocol_id,
    )


def _spec(
    workload: dict[str, Any],
    *,
    primary_problem: dict[str, Any],
    confirmation_problem: dict[str, Any],
) -> KernelWorkloadSpec:
    primary_protocol = KernelBenchmarkProtocol.model_validate(protocol_payload(primary_problem))
    confirmation_protocol = KernelBenchmarkProtocol.model_validate(protocol_payload(confirmation_problem))
    budget = workload["budget"]
    return KernelWorkloadSpec(
        workload_id=workload["workload_id"],
        workload_family=workload["workload_family"],
        problem_id=workload["problem_id"],
        reference_id=digest(workload["reference_identity"]),
        workload_family_id=workload_family_id(workload),
        primary_protocol_id=primary_protocol.protocol_id,
        confirmation_protocol_ids=(confirmation_protocol.protocol_id,),
        protocol_compatibility_id=primary_protocol.compatibility_id,
        required_correctness_slices=("train", "holdout"),
        required_transfer_dimensions=tuple(workload["required_transfer_dimensions"]),
        minimum_case_speedup_vs_incumbent=0.98,
        budget=KernelWorkloadBudget.model_validate(budget),
        reference_implementation=workload["reference_implementation"],
        task_prompt=workload["task_prompt"],
    )


def _generation_budget(spec: KernelWorkloadSpec) -> KernelGenerationBudget:
    return KernelGenerationBudget(
        proposal_cap=spec.budget.proposal_cap,
        max_retries_per_proposal=0,
        max_output_tokens_per_call=4_096,
        max_total_input_tokens=spec.budget.max_total_tokens,
        max_total_output_tokens=spec.budget.max_total_tokens,
        max_total_tokens=spec.budget.max_total_tokens,
        max_cost_usd=spec.budget.max_cost_usd,
        max_wall_seconds=spec.budget.max_wall_seconds,
    )


def _run_workload(
    workload: dict[str, Any],
    *,
    all_workloads: list[dict[str, Any]],
    study_root: Path,
) -> tuple[
    KernelWorkloadSpec,
    KernelWorkloadRunEvidence,
    KernelCandidate,
    KernelCandidate,
    KernelBenchmarkEvaluator,
    KernelBenchmarkEvaluator,
]:
    workload_id = workload["workload_id"]
    contract_root = study_root / "contracts" / workload_id
    primary_problem = problem_payload(workload, role="primary")
    confirmation_problem = problem_payload(workload, role="confirmation")
    primary_path = contract_root / "primary.json"
    confirmation_path = contract_root / "confirmation.json"
    _write_exact_json(primary_path, primary_problem)
    _write_exact_json(confirmation_path, confirmation_problem)
    primary = _make_evaluator(primary_path, problem_id=workload["problem_id"])
    confirmation = _make_evaluator(confirmation_path, problem_id=workload["problem_id"])
    spec = _spec(
        workload,
        primary_problem=primary_problem,
        confirmation_problem=confirmation_problem,
    )
    budget = _generation_budget(spec)
    baseline = KernelCandidate(
        source=baseline_source(all_workloads),
        source_suffix=".py",
        entrypoint="kernel_fn",
    )

    def confirm(candidate: KernelCandidate, incumbent: KernelCandidate) -> KernelBenchmarkObservation | None:
        return _evaluate_final(confirmation, candidate=candidate, baseline=incumbent)

    candidates = tuple(candidate_source(item) for item in workload["candidates"])

    def generate(_prompt: str, generation: int) -> str:
        return candidates[generation]

    run_id = f"{workload_id}-{uuid.uuid4().hex}"
    output_root = study_root / "runs" / workload_id
    runner = KernelEvolutionRunner(
        KernelEvolutionConfig(
            problem_id=workload["problem_id"],
            task_prompt=workload["task_prompt"],
            baseline_source=baseline.source,
            source_suffix=".py",
            entrypoint="kernel_fn",
            min_relative_improvement=0.05,
            require_confidence=True,
            max_p95_regression=0.05,
            max_environment_drift=0.10,
            max_peak_memory_fraction=0.80,
            target_reference_speedup=1.20,
        ),
        generate,
        primary,
        output_root,
        run_id=run_id,
        confirmation_fn=confirm,
        generation_budget=budget,
    )
    result = runner.run(proposals=spec.budget.proposal_cap)
    champion = KernelCandidate(
        source=result.champion_source,
        source_suffix=".py",
        entrypoint="kernel_fn",
    )
    primary_evidence = _evaluate_final(primary, candidate=champion, baseline=baseline)
    confirmation_evidence = _evaluate_final(confirmation, candidate=champion, baseline=baseline)
    status = read_kernel_campaign_status(output_root, run_id, generation_budget=budget)
    run = build_kernel_workload_run_evidence(
        spec=spec,
        result=result,
        primary_observation=primary_evidence,
        confirmation_observation=confirmation_evidence,
        budget_state=status.generation_budget_state,
        strategy_tags=strategy_tags(result.champion_source),
    )
    return spec, run, champion, baseline, primary, confirmation


def _hardware_transfer(
    *,
    workload: dict[str, Any],
    study_root: Path,
    champion: KernelCandidate,
    baseline: KernelCandidate,
) -> KernelTransferEvidence:
    contract_root = study_root / "contracts" / workload["workload_id"]
    primary_problem = problem_payload(workload, role="primary", environment="synthetic-sm100")
    confirmation_problem = problem_payload(workload, role="confirmation", environment="synthetic-sm100")
    primary_path = contract_root / "primary-sm100.json"
    confirmation_path = contract_root / "confirmation-sm100.json"
    _write_exact_json(primary_path, primary_problem)
    _write_exact_json(confirmation_path, confirmation_problem)
    primary = _make_evaluator(primary_path, problem_id=workload["problem_id"])
    confirmation = _make_evaluator(confirmation_path, problem_id=workload["problem_id"])
    return build_kernel_transfer_evidence(
        source_workload_id=workload["workload_id"],
        target_workload_id=workload["workload_id"],
        dimensions=("hardware",),
        primary_observation=_evaluate_final(primary, candidate=champion, baseline=baseline),
        confirmation_observation=_evaluate_final(confirmation, candidate=champion, baseline=baseline),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("runs/kernel-multi-workload"))
    parser.add_argument("--study-id", default=f"study-{uuid.uuid4().hex}")
    args = parser.parse_args()
    manifest = load_manifest(MANIFEST)
    workloads: list[dict[str, Any]] = manifest["workloads"]
    study_root = args.output.resolve() / args.study_id
    study_root.mkdir(parents=True, exist_ok=False)
    _write_exact_json(study_root / "manifest.json", manifest)

    completed = [_run_workload(workload, all_workloads=workloads, study_root=study_root) for workload in workloads]
    specs = tuple(item[0] for item in completed)
    runs = tuple(item[1] for item in completed)
    by_workload = {
        workload["workload_id"]: (workload, *result[2:]) for workload, result in zip(workloads, completed, strict=True)
    }
    transfers: list[KernelTransferEvidence] = []
    for source_run in runs:
        if source_run.disposition != "promoted":
            continue
        source_workload, champion, baseline, _primary, _confirmation = by_workload[source_run.workload_id]
        transfers.append(
            _hardware_transfer(
                workload=source_workload,
                study_root=study_root,
                champion=champion,
                baseline=baseline,
            )
        )
        for target_run in runs:
            if target_run.workload_id == source_run.workload_id:
                continue
            _target_workload, _target_champion, target_baseline, target_primary, target_confirmation = by_workload[
                target_run.workload_id
            ]
            transfers.append(
                build_kernel_transfer_evidence(
                    source_workload_id=source_run.workload_id,
                    target_workload_id=target_run.workload_id,
                    dimensions=("shape", "workload-family"),
                    primary_observation=_evaluate_final(
                        target_primary,
                        candidate=champion,
                        baseline=target_baseline,
                    ),
                    confirmation_observation=_evaluate_final(
                        target_confirmation,
                        candidate=champion,
                        baseline=target_baseline,
                    ),
                )
            )

    report = build_kernel_workload_study_report(
        study_name=manifest["study_name"],
        specs=specs,
        runs=runs,
        transfers=tuple(transfers),
    )
    report_path = study_root / "study_report.json"
    report_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"study: {report.study_id}")
    print(f"report: {report_path}")
    for run in report.workload_runs:
        print(f"{run.workload_id}: {run.disposition}; primary={run.primary.passed}; confirmation={run.confirmation.passed}")
    for assessment in report.champion_assessments:
        print(f"{assessment.source_workload_id}: {assessment.disposition}")
    print(f"portable champions: {len(report.portable_champion_artifact_digests)}")


if __name__ == "__main__":
    main()
