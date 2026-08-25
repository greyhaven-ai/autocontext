"""Run three bounded kernel families through one evolution/study contract.

From ``autocontext/``:

    uv run --frozen python ../examples/kernel_evolution/multi_workload/run.py

The adapter is deterministic and synthetic. The generated report proves
orchestration and evidence invariants; it is not accelerator performance data.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from contract import (
    EVIDENCE_ORIGIN,
    SYNTHETIC_BACKEND_IDENTITY,
    baseline_source,
    candidate_source,
    canonical_digest,
    digest,
    hardware_payload,
    load_manifest,
    problem_payload,
    protocol_payload,
    reserved_seed_commitment,
    strategy_tags,
    validated_workload_id,
    workload_family_id,
)
from contract_runtime import (
    ContractRuntime,
    ContractSnapshot,
    contract_snapshot,
    create_private_directory,
    materialize_contract_runtime,
    materialize_reference_sources,
    portable_file_inventory,
    publish_exact_bundle,
    read_exact_relative,
    retained_relative_working_directory,
    runtime_manifest_failure_fields,
    verify_contract_snapshot,
    write_exact_bytes,
    write_exact_relative,
)
from evidence_runtime import EvidenceRecorder, StudyEvaluator, WorkloadDeadline, evaluate_final, make_evaluator

from autocontext.kernel_evolution import (
    KernelBenchmarkEvaluatorConfig,
    KernelBenchmarkObservation,
    KernelBenchmarkProtocol,
    KernelCampaignJournal,
    KernelCandidate,
    KernelEvolutionConfig,
    KernelEvolutionRunner,
    KernelGenerationBudget,
    KernelHardwareIdentity,
    KernelProtocolReservation,
    KernelStudyDimension,
    KernelTransferEvidence,
    KernelTransferProtocolReservation,
    KernelWorkloadBudget,
    KernelWorkloadRunEvidence,
    KernelWorkloadSpec,
    KernelWorkloadStudyProvenance,
    build_kernel_transfer_evidence,
    build_kernel_workload_run_evidence,
    build_kernel_workload_study_report,
    kernel_generation_receipt_context_digest,
    read_kernel_campaign_status,
)
from autocontext.kernel_evolution.runner_config import decision_policy_from_config

EXAMPLE_DIR = Path(__file__).resolve().parent
MANIFEST = EXAMPLE_DIR / "manifest.json"
ADAPTER = EXAMPLE_DIR / "adapter.py"
CONTRACT = EXAMPLE_DIR / "contract.py"
EVIDENCE_RUNTIME = EXAMPLE_DIR / "evidence_runtime.py"
_SAFE_STUDY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True, slots=True)
class _StudyBinding:
    study_execution_id: str
    manifest_digest: str
    contract_digest: str
    backend_identity: str
    warning: str
    contract_snapshot: ContractSnapshot
    runtime: ContractRuntime


def _encoded_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _write_exact_json(path: Path, payload: dict[str, Any]) -> None:
    write_exact_bytes(path, _encoded_json(payload).encode("utf-8"))


def _write_exact_json_at(directory_fd: int, relative_path: str, payload: dict[str, Any]) -> None:
    write_exact_relative(directory_fd, relative_path, _encoded_json(payload).encode("utf-8"))


def _direct_child(root: Path, component: str) -> Path:
    if not component or component in {".", ".."} or Path(component).name != component:
        raise ValueError("study artifact names must be safe path components")
    absolute_root = root.absolute()
    child = absolute_root / component
    if child.parent != absolute_root:
        raise ValueError("study artifact path escaped its expected parent")
    return child


def _resolve_study_root(output: Path, study_id: str) -> Path:
    if _SAFE_STUDY_ID.fullmatch(study_id) is None:
        raise ValueError("study ID must contain only letters, numbers, '.', '_', or '-' and cannot be a path")
    output_root = output.expanduser().resolve()
    study_root = output_root / study_id
    if study_root.parent != output_root:
        raise ValueError("study ID must be directly below the output directory")
    return study_root


def _contract_digest() -> str:
    return contract_snapshot().contract_digest


def _reserved_problem(
    workload: dict[str, Any],
    *,
    role: Literal["primary", "confirmation"],
    evidence_purpose: str,
    source_workload_id: str,
    environment: str,
    binding: _StudyBinding,
) -> dict[str, Any]:
    seed_commitment = reserved_seed_commitment(
        workload,
        role=role,
        evidence_purpose=evidence_purpose,
        source_workload_id=source_workload_id,
        environment=environment,
        study_execution_id=binding.study_execution_id,
        study_manifest_digest=binding.manifest_digest,
        study_contract_digest=binding.contract_digest,
        study_backend_identity=binding.backend_identity,
    )
    return problem_payload(
        workload,
        role=role,
        environment=environment,
        seed_commitment=seed_commitment,
        evidence_purpose=evidence_purpose,
        source_workload_id=source_workload_id,
        study_execution_id=binding.study_execution_id,
        study_manifest_digest=binding.manifest_digest,
        study_contract_digest=binding.contract_digest,
        study_backend_identity=binding.backend_identity,
        evidence_warning=binding.warning,
    )


def _materialize_problem(
    contract_root: Path,
    name: str,
    payload: dict[str, Any],
    *,
    recorder: EvidenceRecorder | None = None,
) -> Path:
    path = _direct_child(contract_root, f"{name}.json")
    content = _encoded_json(payload).encode("utf-8")
    if recorder is None or recorder.study_directory_fd is None:
        write_exact_bytes(path, content)
    else:
        relative = path.absolute().relative_to(recorder.study_root.absolute())
        write_exact_relative(recorder.study_directory_fd, relative, content)
    return path


@dataclass(frozen=True, slots=True)
class _ProblemPair:
    primary: dict[str, Any]
    confirmation: dict[str, Any]
    path_prefix: str


@dataclass(frozen=True, slots=True)
class _ReservedContracts:
    campaign_primary: dict[str, Any]
    campaign_confirmations: tuple[dict[str, Any], ...]
    final: _ProblemPair
    hardware_transfer: _ProblemPair
    workload_transfers: dict[str, _ProblemPair]

    @property
    def study_pairs(self) -> tuple[_ProblemPair, ...]:
        return (self.final, self.hardware_transfer, *self.workload_transfers.values())


def _problem_pair(
    workload: dict[str, Any],
    *,
    evidence_purpose: str,
    source_workload_id: str,
    environment: str,
    binding: _StudyBinding,
    path_prefix: str,
) -> _ProblemPair:
    def reserve(role: Literal["primary", "confirmation"]) -> dict[str, Any]:
        return _reserved_problem(
            workload,
            role=role,
            evidence_purpose=evidence_purpose,
            source_workload_id=source_workload_id,
            environment=environment,
            binding=binding,
        )

    return _ProblemPair(primary=reserve("primary"), confirmation=reserve("confirmation"), path_prefix=path_prefix)


def _reserve_contracts(
    workload: dict[str, Any],
    *,
    all_workloads: list[dict[str, Any]],
    binding: _StudyBinding,
) -> _ReservedContracts:
    workload_id = validated_workload_id(workload["workload_id"])
    source_ids = tuple(validated_workload_id(source["workload_id"]) for source in all_workloads)
    environment = "synthetic-sm90"
    campaign_primary = _reserved_problem(
        workload,
        role="primary",
        evidence_purpose="campaign-primary",
        source_workload_id=workload_id,
        environment=environment,
        binding=binding,
    )
    campaign_confirmations = tuple(
        _reserved_problem(
            workload,
            role="confirmation",
            evidence_purpose=f"campaign-confirmation-{index:04d}",
            source_workload_id=workload_id,
            environment=environment,
            binding=binding,
        )
        for index in range(1, int(workload["budget"]["generation"]["proposal_cap"]) + 1)
    )
    workload_transfers = {
        source_id: _problem_pair(
            workload,
            evidence_purpose="workload-transfer",
            source_workload_id=source_id,
            environment=environment,
            binding=binding,
            path_prefix=f"transfer-from-{source_id}",
        )
        for source_id in source_ids
        if source_id != workload_id
    }
    return _ReservedContracts(
        campaign_primary=campaign_primary,
        campaign_confirmations=campaign_confirmations,
        final=_problem_pair(
            workload,
            evidence_purpose="final-champion",
            source_workload_id=workload_id,
            environment=environment,
            binding=binding,
            path_prefix="final-champion",
        ),
        hardware_transfer=_problem_pair(
            workload,
            evidence_purpose="hardware-transfer",
            source_workload_id=workload_id,
            environment="synthetic-sm100",
            binding=binding,
            path_prefix="hardware-transfer-sm100",
        ),
        workload_transfers=workload_transfers,
    )


def _materialize_pair(
    contract_root: Path,
    pair: _ProblemPair,
    *,
    recorder: EvidenceRecorder,
) -> tuple[Path, Path]:
    return (
        _materialize_problem(
            contract_root, f"{pair.path_prefix}-primary", pair.primary, recorder=recorder
        ),
        _materialize_problem(
            contract_root, f"{pair.path_prefix}-confirmation", pair.confirmation, recorder=recorder
        ),
    )


def _protocol_reservation(problem: dict[str, Any]) -> KernelProtocolReservation:
    protocol = KernelBenchmarkProtocol.model_validate(protocol_payload(problem))
    hardware = KernelHardwareIdentity.model_validate(hardware_payload(problem))
    return KernelProtocolReservation(
        protocol_id=protocol.protocol_id,
        plan_commitment=protocol.seed_commitment,
        hardware_scope_id=hardware.scope_id,
        execution_environment_id=hardware.execution_environment_id,
    )


def _generation_budget(workload: dict[str, Any]) -> KernelGenerationBudget:
    return KernelGenerationBudget.model_validate(workload["budget"]["generation"])


def _evolution_config(workload: dict[str, Any], baseline: KernelCandidate) -> KernelEvolutionConfig:
    return KernelEvolutionConfig(
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
    )


def _transfer_protocol(
    *,
    source_workload_id: str,
    target_workload_id: str,
    dimensions: tuple[KernelStudyDimension, ...],
    pair: _ProblemPair,
) -> KernelTransferProtocolReservation:
    return KernelTransferProtocolReservation(
        source_workload_id=source_workload_id,
        target_workload_id=target_workload_id,
        dimensions=dimensions,
        primary=_protocol_reservation(pair.primary),
        confirmation=_protocol_reservation(pair.confirmation),
    )


def _spec(
    workload: dict[str, Any],
    *,
    contracts: _ReservedContracts,
    baseline: KernelCandidate,
    generation_budget: KernelGenerationBudget,
) -> KernelWorkloadSpec:
    workload_id = validated_workload_id(workload["workload_id"])
    primary_protocol = KernelBenchmarkProtocol.model_validate(protocol_payload(contracts.campaign_primary))
    primary_reservation = _protocol_reservation(contracts.campaign_primary)
    evolution_config = _evolution_config(workload, baseline)
    statistics = KernelBenchmarkEvaluatorConfig(
        problem_id=workload["problem_id"], min_timing_blocks=10, bootstrap_samples=2_000
    ).statistics_policy
    transfer_protocols = [
        _transfer_protocol(
            source_workload_id=workload_id,
            target_workload_id=workload_id,
            dimensions=("hardware",),
            pair=contracts.hardware_transfer,
        )
    ]
    transfer_protocols.extend(
        _transfer_protocol(
            source_workload_id=source_id,
            target_workload_id=workload_id,
            dimensions=("shape", "workload-family"),
            pair=pair,
        )
        for source_id, pair in contracts.workload_transfers.items()
    )
    return KernelWorkloadSpec(
        workload_id=workload_id,
        workload_family=workload["workload_family"],
        problem_id=workload["problem_id"],
        reference_id=digest(workload["reference_identity"]),
        reference_artifact_digest=baseline.artifact_digest,
        reference_source_digest=baseline.source_digest,
        reference_source_suffix=baseline.source_suffix,
        reference_entrypoint=baseline.entrypoint,
        workload_family_id=workload_family_id(workload),
        shape_profile_id=contracts.campaign_primary["shape_profile_id"],
        execution_environment_id=primary_reservation.execution_environment_id,
        decision_policy=decision_policy_from_config(evolution_config, statistics, require_confirmation=True),
        primary_protocol=primary_reservation,
        confirmation_protocols=tuple(_protocol_reservation(item) for item in contracts.campaign_confirmations),
        final_primary_protocol=_protocol_reservation(contracts.final.primary),
        final_confirmation_protocol=_protocol_reservation(contracts.final.confirmation),
        transfer_protocols=tuple(transfer_protocols),
        protocol_compatibility_id=primary_protocol.compatibility_id,
        required_correctness_slices=("train", "holdout"),
        required_benchmark_cases=tuple(f"{case['split']}:{case['name']}" for case in workload["cases"]),
        required_transfer_dimensions=tuple(workload["required_transfer_dimensions"]),
        minimum_case_speedup_vs_incumbent=0.98,
        budget=KernelWorkloadBudget(
            generation_budget=generation_budget,
            max_workload_wall_seconds=workload["budget"]["max_workload_wall_seconds"],
            max_workload_cost_usd=workload["budget"]["max_workload_cost_usd"],
        ),
        reference_implementation=workload["reference_implementation"],
        task_prompt=workload["task_prompt"],
    )


def _study_evaluator(
    problem_path: Path,
    *,
    problem_payload: dict[str, Any],
    problem_id: str,
    deadline: WorkloadDeadline,
    recorder: EvidenceRecorder,
    stream_id: str,
    binding: _StudyBinding,
    single_use: bool = True,
) -> StudyEvaluator:
    sources = {source.logical_path: source.content for source in binding.contract_snapshot.sources}
    contract_path = binding.runtime.root / "example" / "contract.py"
    problem_content = _encoded_json(problem_payload).encode("utf-8")
    return make_evaluator(
        problem_path,
        problem_content=problem_content,
        problem_id=problem_id,
        deadline=deadline,
        recorder=recorder,
        stream_id=stream_id,
        adapter=binding.runtime.adapter,
        immutable_paths=(
            binding.runtime.adapter,
            contract_path,
            problem_path,
        ),
        expected_immutable_files={
            binding.runtime.adapter: sources["example/adapter.py"],
            contract_path: sources["example/contract.py"],
            problem_path: problem_content,
        },
        single_use=single_use,
    )


@dataclass(frozen=True, slots=True)
class _WorkloadPlan:
    workload: dict[str, Any]
    contracts: _ReservedContracts
    spec: KernelWorkloadSpec
    baseline: KernelCandidate
    generation_budget: KernelGenerationBudget


def _expected_portable_digests(
    plans: tuple[_WorkloadPlan, ...],
    *,
    snapshot: ContractSnapshot,
    recorder: EvidenceRecorder,
    reference_sources: tuple[dict[str, Any], ...],
    manifest_digest: str,
    evidence_index_digest: str,
    report_content: bytes,
) -> dict[str, str]:
    expected = {
        "manifest.json": manifest_digest,
        "study_report.json": digest(report_content),
        "evidence/index.json": evidence_index_digest,
        "contract-runtime/manifest.json": digest(_encoded_json(snapshot.manifest)),
    }
    expected.update(
        (f"contract-runtime/{source.logical_path}", digest(source.content))
        for source in snapshot.sources
    )
    for item in reference_sources:
        expected[str(item["path"])] = str(item["file_digest"])
    for record in recorder.records:
        expected[str(record["observation_path"])] = str(record["observation_digest"])
        if record["report_path"] is not None:
            expected[str(record["report_path"])] = str(record["report_digest"])
    for plan in plans:
        root = f"contracts/{plan.spec.workload_id}"
        contracts = plan.contracts
        expected[f"{root}/campaign-primary.json"] = digest(_encoded_json(contracts.campaign_primary))
        for index, problem in enumerate(contracts.campaign_confirmations, start=1):
            expected[f"{root}/campaign-confirmation-{index:04d}.json"] = digest(_encoded_json(problem))
        for pair in contracts.study_pairs:
            expected[f"{root}/{pair.path_prefix}-primary.json"] = digest(_encoded_json(pair.primary))
            expected[f"{root}/{pair.path_prefix}-confirmation.json"] = digest(_encoded_json(pair.confirmation))
    for record in recorder.records:
        contract_path = str(record["problem_contract_path"])
        if expected.get(contract_path) != record["problem_contract_digest"]:
            raise RuntimeError("evidence record problem digest disagrees with its reserved contract")
    return expected


def _plan_workload(
    workload: dict[str, Any],
    *,
    all_workloads: list[dict[str, Any]],
    binding: _StudyBinding,
    baseline: KernelCandidate,
) -> _WorkloadPlan:
    contracts = _reserve_contracts(workload, all_workloads=all_workloads, binding=binding)
    generation_budget = _generation_budget(workload)
    spec = _spec(workload, contracts=contracts, baseline=baseline, generation_budget=generation_budget)
    return _WorkloadPlan(workload, contracts, spec, baseline, generation_budget)


def _bind_workload_specs_digest(contracts: _ReservedContracts, workload_specs_digest: str) -> None:
    problems = (
        contracts.campaign_primary,
        *contracts.campaign_confirmations,
        *(problem for pair in contracts.study_pairs for problem in (pair.primary, pair.confirmation)),
    )
    for problem in problems:
        problem["workload_specs_digest"] = workload_specs_digest


@dataclass(frozen=True, slots=True)
class _WorkloadExecution:
    workload: dict[str, Any]
    contracts: _ReservedContracts
    spec: KernelWorkloadSpec
    run: KernelWorkloadRunEvidence
    champion: KernelCandidate
    baseline: KernelCandidate
    deadline: WorkloadDeadline


def _run_workload(
    plan: _WorkloadPlan,
    *,
    study_root: Path,
    binding: _StudyBinding,
    recorder: EvidenceRecorder,
) -> _WorkloadExecution:
    workload = plan.workload
    contracts = plan.contracts
    spec = plan.spec
    baseline = plan.baseline
    budget = plan.generation_budget
    workload_id = validated_workload_id(workload["workload_id"])
    deadline = WorkloadDeadline(workload_id, float(workload["budget"]["max_workload_wall_seconds"]))
    workload_started = time.monotonic()
    with deadline.active():
        contract_root = _direct_child(study_root / "contracts", workload_id)
        primary_path = _materialize_problem(
            contract_root, "campaign-primary", contracts.campaign_primary, recorder=recorder
        )
        confirmation_paths = tuple(
            _materialize_problem(
                contract_root,
                f"campaign-confirmation-{index:04d}",
                problem,
                recorder=recorder,
            )
            for index, problem in enumerate(contracts.campaign_confirmations, start=1)
        )
        for pair in (contracts.hardware_transfer, *contracts.workload_transfers.values()):
            _materialize_pair(contract_root, pair, recorder=recorder)

        primary = _study_evaluator(
            primary_path,
            problem_payload=contracts.campaign_primary,
            problem_id=workload["problem_id"],
            deadline=deadline,
            recorder=recorder,
            stream_id=f"campaign/{workload_id}/primary",
            binding=binding,
            single_use=False,
        )
        confirmations = tuple(
            _study_evaluator(
                path,
                problem_payload=problem,
                problem_id=workload["problem_id"],
                deadline=deadline,
                recorder=recorder,
                stream_id=f"campaign/{workload_id}/confirmation-{index:04d}",
                binding=binding,
            )
            for index, (path, problem) in enumerate(
                zip(confirmation_paths, contracts.campaign_confirmations, strict=True), start=1
            )
        )
        confirmation_index = 0

        def confirm(candidate: KernelCandidate, incumbent: KernelCandidate) -> KernelBenchmarkObservation | None:
            nonlocal confirmation_index
            if confirmation_index >= len(confirmations):
                raise RuntimeError("campaign requested more confirmation looks than its reserved proposal budget")
            evaluator = confirmations[confirmation_index]
            confirmation_index += 1
            return evaluate_final(evaluator, candidate=candidate, baseline=incumbent).observation

        candidates = tuple(candidate_source(item) for item in workload["candidates"])

        def generate(_prompt: str, generation: int) -> str:
            return candidates[generation]

        run_suffix = canonical_digest(
            {
                "kind": "autocontext.synthetic-kernel-campaign/v1",
                "workload_id": workload_id,
                "manifest_digest": binding.manifest_digest,
                "contract_digest": binding.contract_digest,
            }
        ).split(":", maxsplit=1)[1][:24]
        run_id = f"{workload_id}-{run_suffix}"
        directory_fd = recorder.study_directory_fd
        if directory_fd is None:
            raise RuntimeError("workload diagnostics require a retained study directory")
        with retained_relative_working_directory(
            directory_fd, Path("runs") / workload_id
        ) as anchored_output_root:
            runner = KernelEvolutionRunner(
                _evolution_config(workload, baseline),
                generate,
                primary,
                anchored_output_root,
                run_id=run_id,
                confirmation_fn=confirm,
                confirmation_identity=canonical_digest(
                    {
                        "kind": "autocontext.synthetic-kernel-confirmation-reservations/v1",
                        "reservations": [
                            _protocol_reservation(problem).model_dump(mode="json")
                            for problem in contracts.campaign_confirmations
                        ],
                    }
                ),
                generation_budget=budget,
            )
            if deadline.remaining_seconds <= 0:
                raise RuntimeError(f"workload {workload_id!r} exhausted its wall budget before campaign dispatch")
            result = runner.run(proposals=budget.proposal_cap)
            champion = KernelCandidate(source=result.champion_source, source_suffix=".py", entrypoint="kernel_fn")
            status = read_kernel_campaign_status(anchored_output_root, run_id, generation_budget=budget)
            generation_results, generation_failures = KernelCampaignJournal(
                anchored_output_root / run_id,
                run_id,
            ).generation_activity()
        generation_context_digest = kernel_generation_receipt_context_digest(
            study_execution_id=binding.study_execution_id,
            workload_spec_id=spec.spec_id,
            run_id=result.run_id,
            generation_budget_id=spec.budget.generation_budget.budget_id,
            generation_results=tuple(generation_results),
            generation_failures=generation_failures,
        )
        for problem in (contracts.final.primary, contracts.final.confirmation):
            problem["generation_receipt_context_digest"] = generation_context_digest

        final_primary_path, final_confirmation_path = _materialize_pair(
            contract_root, contracts.final, recorder=recorder
        )
        final_primary = _study_evaluator(
            final_primary_path,
            problem_payload=contracts.final.primary,
            problem_id=workload["problem_id"],
            deadline=deadline,
            recorder=recorder,
            stream_id=f"final/{workload_id}/primary",
            binding=binding,
        )
        final_confirmation = _study_evaluator(
            final_confirmation_path,
            problem_payload=contracts.final.confirmation,
            problem_id=workload["problem_id"],
            deadline=deadline,
            recorder=recorder,
            stream_id=f"final/{workload_id}/confirmation",
            binding=binding,
        )
        primary_evidence = evaluate_final(final_primary, candidate=champion, baseline=baseline)
        confirmation_evidence = evaluate_final(final_confirmation, candidate=champion, baseline=baseline)
        workload_wall_seconds = time.monotonic() - workload_started
        if deadline.remaining_seconds <= 0:
            raise RuntimeError(f"workload {workload_id!r} exhausted its end-to-end wall budget")
        final_wall_seconds = primary_evidence.wall_seconds + confirmation_evidence.wall_seconds
        runner_wall_seconds = workload_wall_seconds - final_wall_seconds
        if runner_wall_seconds <= 0:
            raise RuntimeError("workload wall-clock phase accounting is inconsistent")
    run = build_kernel_workload_run_evidence(
        study_execution_id=binding.study_execution_id,
        spec=spec,
        result=result,
        primary_observation=primary_evidence.observation,
        confirmation_observation=confirmation_evidence.observation,
        generation_results=tuple(generation_results),
        generation_failures=generation_failures,
        budget_state=status.generation_budget_state,
        proposals_requested=len(generation_results) + int(bool(generation_failures)),
        runner_wall_seconds=runner_wall_seconds,
        runner_cost_usd=float(status.generation_budget_state.cost_usd),
        primary_evaluation_wall_seconds=primary_evidence.wall_seconds,
        confirmation_evaluation_wall_seconds=confirmation_evidence.wall_seconds,
        primary_evaluation_cost_usd=primary_evidence.cost_usd,
        confirmation_evaluation_cost_usd=confirmation_evidence.cost_usd,
        strategy_tags=strategy_tags(result.champion_source),
    )
    return _WorkloadExecution(workload, contracts, spec, run, champion, baseline, deadline)


def _transfer(
    *,
    source: _WorkloadExecution,
    target: _WorkloadExecution,
    pair: _ProblemPair,
    dimensions: tuple[KernelStudyDimension, ...],
    study_root: Path,
    recorder: EvidenceRecorder,
    binding: _StudyBinding,
) -> KernelTransferEvidence:
    source_id = validated_workload_id(source.spec.workload_id)
    target_id = validated_workload_id(target.spec.workload_id)
    transfer_started = time.monotonic()
    with source.deadline.active():
        target_contract_root = _direct_child(study_root / "contracts", target_id)
        primary_path, confirmation_path = _materialize_pair(
            target_contract_root, pair, recorder=recorder
        )
        primary = _study_evaluator(
            primary_path,
            problem_payload=pair.primary,
            problem_id=target.spec.problem_id,
            deadline=source.deadline,
            recorder=recorder,
            stream_id=f"transfer/{source_id}-to-{target_id}/{'-'.join(dimensions)}/primary",
            binding=binding,
        )
        confirmation = _study_evaluator(
            confirmation_path,
            problem_payload=pair.confirmation,
            problem_id=target.spec.problem_id,
            deadline=source.deadline,
            recorder=recorder,
            stream_id=f"transfer/{source_id}-to-{target_id}/{'-'.join(dimensions)}/confirmation",
            binding=binding,
        )
        if source.deadline.remaining_seconds <= 0:
            raise RuntimeError(f"workload {source_id!r} exhausted its wall budget before transfer dispatch")
        primary_evidence = evaluate_final(primary, candidate=source.champion, baseline=target.baseline)
        if source.deadline.remaining_seconds <= 0:
            raise RuntimeError(f"workload {source_id!r} exhausted its wall budget during transfer")
        confirmation_evidence = evaluate_final(
            confirmation, candidate=source.champion, baseline=target.baseline
        )
        transfer_wall_seconds = time.monotonic() - transfer_started
        if source.deadline.remaining_seconds <= 0:
            raise RuntimeError(f"workload {source_id!r} exhausted its wall budget during transfer")
        evaluation_wall_seconds = primary_evidence.wall_seconds + confirmation_evidence.wall_seconds
        orchestration_wall_seconds = transfer_wall_seconds - evaluation_wall_seconds
        if orchestration_wall_seconds < -1e-9:
            raise RuntimeError("transfer wall-clock phase accounting is inconsistent")
        orchestration_wall_seconds = max(0.0, orchestration_wall_seconds)
    return build_kernel_transfer_evidence(
        source_workload_id=source_id,
        target_workload_id=target_id,
        dimensions=dimensions,
        primary_observation=primary_evidence.observation,
        confirmation_observation=confirmation_evidence.observation,
        primary_evaluation_wall_seconds=primary_evidence.wall_seconds + orchestration_wall_seconds,
        confirmation_evaluation_wall_seconds=confirmation_evidence.wall_seconds,
        primary_evaluation_cost_usd=primary_evidence.cost_usd,
        confirmation_evaluation_cost_usd=confirmation_evidence.cost_usd,
        primary_reservation=_protocol_reservation(pair.primary),
        confirmation_reservation=_protocol_reservation(pair.confirmation),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("runs/kernel-multi-workload"))
    parser.add_argument("--study-id", default=f"study-{uuid.uuid4().hex}")
    args = parser.parse_args()
    manifest = load_manifest(MANIFEST)
    manifest["study_execution_id"] = digest(secrets.token_bytes(32))
    workloads: list[dict[str, Any]] = manifest["workloads"]
    try:
        study_root = _resolve_study_root(args.output, args.study_id)
    except ValueError as exc:
        parser.error(str(exc))
    study_root_fd = create_private_directory(study_root)
    snapshot: ContractSnapshot | None = None
    try:
        snapshot = contract_snapshot()
        _write_exact_json_at(study_root_fd, "manifest.json", manifest)
        runtime = materialize_contract_runtime(study_root, snapshot, directory_fd=study_root_fd)
        reference_sources = materialize_reference_sources(
            study_root, workloads, directory_fd=study_root_fd
        )
        verify_contract_snapshot(snapshot, runtime, directory_fd=study_root_fd)
        binding = _StudyBinding(
            study_execution_id=manifest["study_execution_id"],
            manifest_digest=digest(_encoded_json(manifest)),
            contract_digest=snapshot.contract_digest,
            backend_identity=SYNTHETIC_BACKEND_IDENTITY,
            warning=manifest["warning"],
            contract_snapshot=snapshot,
            runtime=runtime,
        )
        baseline = KernelCandidate(source=baseline_source(workloads), source_suffix=".py", entrypoint="kernel_fn")
        plans = tuple(
            _plan_workload(workload, all_workloads=workloads, binding=binding, baseline=baseline)
            for workload in workloads
        )
        workload_specs_digest = canonical_digest(
            {
                "schema_version": "autocontext.kernel-workload-spec-set/v1",
                "workload_specs": [plan.spec.model_dump(mode="json") for plan in plans],
            }
        )
        for plan in plans:
            _bind_workload_specs_digest(plan.contracts, workload_specs_digest)
        recorder = EvidenceRecorder(
            study_root=study_root,
            study_execution_id=binding.study_execution_id,
            workload_specs_digest=workload_specs_digest,
            study_manifest_digest=binding.manifest_digest,
            study_contract_digest=binding.contract_digest,
            study_backend_identity=binding.backend_identity,
            evidence_warning=binding.warning,
            study_directory_fd=study_root_fd,
        )
    except BaseException as exc:
        try:
            _write_exact_json_at(
                study_root_fd,
                "study_failure.json",
                {
                    "schema_version": "autocontext.synthetic-kernel-study-failure/v1",
                    "evidence_origin": EVIDENCE_ORIGIN,
                    "study_execution_id": manifest["study_execution_id"],
                    "study_manifest_digest": digest(_encoded_json(manifest)),
                    "study_contract_digest": snapshot.contract_digest if snapshot is not None else None,
                    "study_backend_identity": SYNTHETIC_BACKEND_IDENTITY,
                    "evidence_warning": manifest["warning"],
                    "failed_stage": "study setup",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "evidence_index_digest": None,
                    "evidence_index_error": "evidence recorder was not initialized",
                },
            )
        except BaseException as failure_exc:
            exc.add_note(f"terminal study failure receipt could not be written: {failure_exc}")
        finally:
            os.close(study_root_fd)
        raise
    assert snapshot is not None
    stage = "workload campaigns"
    try:
        completed = tuple(
            _run_workload(
                plan,
                study_root=study_root,
                binding=binding,
                recorder=recorder,
            )
            for plan in plans
        )
        by_workload = {item.spec.workload_id: item for item in completed}
        transfers: list[KernelTransferEvidence] = []
        stage = "transfer evaluations"
        for source in completed:
            if source.run.disposition != "promoted":
                continue
            transfers.append(
                _transfer(
                    source=source,
                    target=source,
                    pair=source.contracts.hardware_transfer,
                    dimensions=("hardware",),
                    study_root=study_root,
                    recorder=recorder,
                    binding=binding,
                )
            )
            for target_id, target in by_workload.items():
                if target_id == source.spec.workload_id:
                    continue
                transfers.append(
                    _transfer(
                        source=source,
                        target=target,
                        pair=target.contracts.workload_transfers[source.spec.workload_id],
                        dimensions=("shape", "workload-family"),
                        study_root=study_root,
                        recorder=recorder,
                        binding=binding,
                    )
                )

        stage = "contract runtime verification"
        verify_contract_snapshot(
            binding.contract_snapshot, binding.runtime, directory_fd=study_root_fd
        )
        stage = "evidence index"
        evidence_index_digest = recorder.write_index()
        stage = "study report"
        report = build_kernel_workload_study_report(
            study_name=manifest["study_name"],
            provenance=KernelWorkloadStudyProvenance(
                evidence_kind=EVIDENCE_ORIGIN,
                study_execution_id=binding.study_execution_id,
                workload_specs_digest=workload_specs_digest,
                manifest_digest=binding.manifest_digest,
                contract_digest=binding.contract_digest,
                evidence_index_digest=evidence_index_digest,
                backend_identity=binding.backend_identity,
                warning=binding.warning,
            ),
            specs=tuple(item.spec for item in completed),
            runs=tuple(item.run for item in completed),
            transfers=tuple(transfers),
        )
        report_path = study_root / "study_report.json"
        artifacts_path = study_root / "study_artifacts.json"
        report_content = _encoded_json(report.model_dump(mode="json")).encode("utf-8")
        stage = "artifact manifest"
        portable_files = portable_file_inventory(
            study_root,
            report_content=report_content,
            directory_fd=study_root_fd,
        )
        inventory = {item["path"]: item for item in portable_files}
        expected_digests = _expected_portable_digests(
            plans,
            snapshot=binding.contract_snapshot,
            recorder=recorder,
            reference_sources=reference_sources,
            manifest_digest=binding.manifest_digest,
            evidence_index_digest=evidence_index_digest,
            report_content=report_content,
        )
        observed_digests = {path: str(item["digest"]) for path, item in inventory.items()}
        if observed_digests != expected_digests:
            raise RuntimeError("portable artifact inventory disagrees with the exact study inputs")
        runtime_manifest_path = runtime.manifest_path.relative_to(study_root).as_posix()
        artifacts_content = _encoded_json(
            {
                "schema_version": "autocontext.synthetic-kernel-study-artifacts/v2",
                "evidence_origin": EVIDENCE_ORIGIN,
                "study_execution_id": binding.study_execution_id,
                "workload_specs_digest": workload_specs_digest,
                "study_id": report.study_id,
                "study_manifest_digest": binding.manifest_digest,
                "study_contract_digest": binding.contract_digest,
                "contract_runtime_manifest_path": runtime_manifest_path,
                "contract_runtime_manifest_file_digest": inventory[runtime_manifest_path]["digest"],
                "reference_sources": reference_sources,
                "study_report_digest": digest(report_content),
                "evidence_index_digest": evidence_index_digest,
                "portable_files": portable_files,
                "portable_artifacts": [
                    "manifest.json",
                    "contract-runtime/",
                    "sources/",
                    "contracts/",
                    "evidence/",
                    "study_report.json",
                    "study_artifacts.json",
                ],
                "local_diagnostics_warning": (
                    "runs/ contains host-local command and filesystem paths; it is not part of the portable identity."
                ),
            }
        ).encode("utf-8")
        stage = "success publication"
        publish_exact_bundle(
            study_root,
            ((report_path.name, report_content), (artifacts_path.name, artifacts_content)),
            directory_fd=study_root_fd,
            expected_portable_files=portable_files,
            report_content=report_content,
        )
    except BaseException as exc:
        publication_committed = False
        if stage == "success publication":
            try:
                publication_committed = (
                    read_exact_relative(study_root_fd, report_path.name) == report_content
                    and read_exact_relative(study_root_fd, artifacts_path.name) == artifacts_content
                    and portable_file_inventory(
                        study_root,
                        report_content=report_content,
                        directory_fd=study_root_fd,
                    )
                    == portable_files
                )
            except BaseException:
                publication_committed = False
        if publication_committed:
            os.close(study_root_fd)
            raise
        try:
            evidence_index_digest = recorder.write_index()
            index_error = None
        except BaseException as index_exc:
            evidence_index_digest = None
            index_error = f"{type(index_exc).__name__}: {index_exc}"
        try:
            _write_exact_json_at(
                study_root_fd,
                "study_failure.json",
                {
                    "schema_version": "autocontext.synthetic-kernel-study-failure/v1",
                    "evidence_origin": EVIDENCE_ORIGIN,
                    "study_execution_id": binding.study_execution_id,
                    "workload_specs_digest": workload_specs_digest,
                    "study_manifest_digest": binding.manifest_digest,
                    "study_contract_digest": binding.contract_digest,
                    "study_backend_identity": binding.backend_identity,
                    "evidence_warning": binding.warning,
                    **runtime_manifest_failure_fields(study_root, runtime, directory_fd=study_root_fd),
                    "failed_stage": stage,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "evidence_index_digest": evidence_index_digest,
                    "evidence_index_error": index_error,
                },
            )
        except BaseException as failure_exc:
            exc.add_note(f"terminal study failure receipt could not be written: {failure_exc}")
        finally:
            os.close(study_root_fd)
        raise

    os.close(study_root_fd)
    print(f"WARNING: {binding.warning}")
    print(f"study: {report.study_id}")
    print(f"report: {report_path}")
    print(f"evidence index: {study_root / 'evidence' / 'index.json'}")
    for run in report.workload_runs:
        print(f"{run.workload_id}: {run.disposition}; primary={run.primary.passed}; confirmation={run.confirmation.passed}")
    for assessment in report.champion_assessments:
        print(f"{assessment.source_workload_id}: {assessment.disposition}")
    print(f"portable champions: {len(report.portable_champion_artifact_digests)}")


if __name__ == "__main__":
    main()
