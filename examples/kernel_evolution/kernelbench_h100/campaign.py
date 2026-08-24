#!/usr/bin/env python3
"""Run a bounded, autonomous or mailbox-driven H100 kernel campaign.

From the ``autocontext/`` Python package directory, see ``README.md`` for the
full commands. Provider mode uses AutoContext's provider registry with durable
per-call accounting. Mailbox mode writes the exact recursive prompt and waits
for an operator-authored ``candidate_N.py`` response, numbered from zero.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from types import FrameType
from typing import Any

from production_runtime import (
    H100DockerRuntimeConfig,
    ProductionEvaluatorBoundaryUnavailable,
    _compose_docker_evaluator,
    require_protected_evaluator_boundary,
)
from profile_contract import (
    PROFILE_NAMES,
    PROFILES,
    STRICT_PROFILE,
    assert_fresh_plans,
    load_private_plan,
    private_plan_commitment,
    profile_output_root,
)

from autocontext.kernel_evolution import (
    DockerKernelWorkerLimits,
    KernelBenchmarkEvaluator,
    KernelBenchmarkObservation,
    KernelCalibrationReport,
    KernelCandidate,
    KernelDecisionPolicy,
    KernelDerivedStatisticsReceipt,
    KernelEvolutionConfig,
    KernelEvolutionResult,
    KernelEvolutionRunner,
    KernelGenerationBudget,
    KernelGenerationCancelled,
    KernelGenerationResult,
    KernelSequentialTestingPolicy,
    KernelStatisticsPolicy,
    ProviderKernelGenerator,
    build_generation_result,
    build_profile_evidence_envelope,
    calibrate_kernel_promotion,
    content_digest,
    read_authority_hmac_secret,
    read_kernel_campaign_status,
    verify_authority_receipt,
    verify_profile_evidence_envelope,
)
from autocontext.providers.base import CompletionResult
from autocontext.providers.registry import create_provider

PROBLEM_ID = "kernelbench-v0.1-level1-1-matmul-profiled-h100-v1"
MAX_PROPOSALS = 10
MAX_WAIT_SECONDS = 86_400.0
MAX_CANDIDATE_BYTES = 1_000_000
POLL_SECONDS = 1.0


def _bounded_proposals(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("proposals must be an integer") from exc
    if not 1 <= value <= MAX_PROPOSALS:
        raise argparse.ArgumentTypeError(f"proposals must be between 1 and {MAX_PROPOSALS}")
    return value


def _bounded_timeout(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("candidate wait timeout must be a number") from exc
    if not math.isfinite(value) or not 0 < value <= MAX_WAIT_SECONDS:
        raise argparse.ArgumentTypeError(f"candidate wait timeout must be positive and at most {MAX_WAIT_SECONDS:g} seconds")
    return value


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer") from exc
    if value < 1:
        raise argparse.ArgumentTypeError("value must be positive")
    return value


def _non_negative_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer") from exc
    if value < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return value


def _positive_float(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a number") from exc
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("value must be positive and finite")
    return value


def _validate_confirmation_schedule(
    primary_plan: dict[str, Any],
    confirmation_plans: tuple[dict[str, Any], ...],
    *,
    proposals: int,
) -> None:
    if len(confirmation_plans) < proposals:
        raise ValueError(
            f"production confirmation requires at least one fresh plan per proposal; "
            f"received {len(confirmation_plans)} for {proposals} proposals"
        )
    scheduled_plans = (primary_plan, *confirmation_plans)
    for index, plan in enumerate(scheduled_plans):
        for later in scheduled_plans[index + 1 :]:
            assert_fresh_plans(plan, later)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _atomic_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _candidate(source: str) -> KernelCandidate:
    """Build identities through the production v2 contract."""
    return KernelCandidate(source=source, source_suffix=".py", entrypoint="kernel_fn")


class MailboxGenerator:
    """Persist each recursive prompt and wait for one stable source file."""

    provider_id = "mailbox"
    model = "operator-supplied"
    system_prompt = "Operator mailbox source generation."
    supports_claim_resume = True

    def __init__(
        self,
        mailbox: Path,
        *,
        timeout_seconds: float,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> None:
        self._mailbox = mailbox
        self._timeout_seconds = timeout_seconds
        self._cancelled = cancellation_requested or (lambda: False)
        self.transport_identity = content_digest(str(mailbox.resolve()))

    def __call__(self, prompt: str, generation: int) -> KernelGenerationResult:
        number = generation
        prompt_path = self._mailbox / f"prompt_{number}.md"
        candidate_path = self._mailbox / f"candidate_{number}.py"
        receipt_path = self._mailbox / f"accepted_candidate_{number}.json"
        if prompt_path.is_symlink() or (prompt_path.exists() and not prompt_path.is_file()):
            raise ValueError(f"prompt mailbox path must be a regular non-symlink file: {prompt_path}")
        if prompt_path.exists():
            if prompt_path.read_bytes() != prompt.encode("utf-8"):
                raise ValueError(f"resumed mailbox prompt changed for generation {number}")
            print(f"resuming mailbox generation {number}; waiting for {candidate_path}", flush=True)
        else:
            if candidate_path.exists() or candidate_path.is_symlink() or receipt_path.exists() or receipt_path.is_symlink():
                raise FileExistsError(f"mailbox generation {number} has candidate state without its prompt")
            _atomic_text(prompt_path, prompt)
            print(f"\n===== AUTOCONTEXT KERNEL PROMPT {number} ({prompt_path}) =====", flush=True)
            print(prompt, flush=True)
            print(f"===== END PROMPT {number}; WAITING FOR {candidate_path} =====\n", flush=True)

        started = time.monotonic()
        deadline = started + self._timeout_seconds
        while True:
            if self._cancelled():
                raise KernelGenerationCancelled(
                    f"kernel campaign stop requested while waiting for {candidate_path}"
                )
            if candidate_path.is_symlink():
                raise ValueError(f"candidate mailbox file must not be a symlink: {candidate_path}")
            if candidate_path.exists():
                source = self._read_stable_candidate(candidate_path, deadline=deadline)
                candidate = _candidate(source)
                result = build_generation_result(
                    proposal_index=generation + 1,
                    provider=self.provider_id,
                    model=self.model,
                    system_prompt=self.system_prompt,
                    prompt=prompt,
                    completion=CompletionResult(
                        text=source,
                        model=self.model,
                        cost_usd=0.0,
                        stop_reason="operator-submitted",
                    ),
                    source_suffix=".py",
                    entrypoint="kernel_fn",
                    latency_seconds=max(0.0, time.monotonic() - started),
                    max_source_bytes=MAX_CANDIDATE_BYTES,
                )
                mailbox_receipt = {
                    "schema_version": "autocontext.kernel-mailbox-receipt/v2",
                    "generation": number,
                    "candidate_path": str(candidate_path),
                    "artifact_identity_version": candidate.artifact_identity_version,
                    "artifact_digest": candidate.artifact_digest,
                    "source_digest": candidate.source_digest,
                    "source_bytes": len(candidate.source_bytes),
                }
                if receipt_path.is_symlink() or (receipt_path.exists() and not receipt_path.is_file()):
                    raise ValueError(f"mailbox receipt must be a regular non-symlink file: {receipt_path}")
                if receipt_path.exists():
                    if json.loads(receipt_path.read_text(encoding="utf-8")) != mailbox_receipt:
                        raise ValueError(f"resumed mailbox receipt changed for generation {number}")
                else:
                    _atomic_json(receipt_path, mailbox_receipt)
                print(f"accepted candidate {number}: {candidate.artifact_digest}", flush=True)
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"timed out after {self._timeout_seconds:g}s waiting for {candidate_path}")
            time.sleep(min(POLL_SECONDS, remaining))

    @staticmethod
    def _read_stable_candidate(path: Path, *, deadline: float) -> str:
        while True:
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"candidate mailbox path must be a regular non-symlink file: {path}")
            before = path.stat()
            if before.st_size > MAX_CANDIDATE_BYTES:
                raise ValueError(f"candidate exceeds the {MAX_CANDIDATE_BYTES}-byte limit: {path}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"candidate did not become stable before the deadline: {path}")
            time.sleep(min(0.25, remaining))
            payload = path.read_bytes()
            after = path.stat()
            if before.st_size == after.st_size == len(payload) and before.st_mtime_ns == after.st_mtime_ns:
                source = payload.decode("utf-8")
                if not source.strip():
                    raise ValueError(f"candidate source is empty: {path}")
                return source


def _make_evaluator(
    *,
    runtime: H100DockerRuntimeConfig,
    bundle: Path,
    adapter: Path,
    autokernel_root: Path,
    precision_profile: str,
    private_plan: Path,
    plan_commitment: str,
    proposal_cap: int,
    familywise_alpha: float,
) -> KernelBenchmarkEvaluator:
    return _compose_docker_evaluator(
        runtime=runtime,
        bundle=bundle,
        adapter_name=adapter.name,
        autokernel_root=autokernel_root,
        private_plan=private_plan,
        problem_id=PROBLEM_ID,
        precision_profile=precision_profile,
        plan_commitment=plan_commitment,
        proposal_cap=proposal_cap,
        familywise_alpha=familywise_alpha,
    )


def _progress(run_dir: Path) -> dict[str, int]:
    lineage = run_dir / "lineage.jsonl"
    attempts = len(lineage.read_text(encoding="utf-8").splitlines()) if lineage.exists() else 0
    return {"attempts_persisted": attempts, "proposals_persisted": max(0, attempts - 1)}


def _best_effort_progress(run_dir: Path) -> dict[str, int]:
    """Keep optional progress telemetry from suppressing a terminal status."""
    try:
        return _progress(run_dir)
    except (OSError, UnicodeError):
        return {}


def _require_complete_v4_result_chain(result: KernelEvolutionResult) -> None:
    """Reject downgraded or partially upgraded evidence before profile export."""
    if (
        result.schema_version != "autocontext.kernel-result/v4"
        or result.decision_policy is None
        or result.decision_policy_id != result.decision_policy.policy_id
    ):
        raise RuntimeError("H100 profile evidence requires a complete v4 result chain")
    for attempt in result.attempts:
        observation = attempt.observation
        report = observation.report
        if (
            attempt.schema_version != "autocontext.kernel-lineage/v4"
            or attempt.decision_policy != result.decision_policy
            or attempt.decision_policy_id != result.decision_policy_id
            or attempt.primary_decision is None
            or attempt.promotion_decision is None
        ):
            raise RuntimeError("H100 profile evidence requires a complete v4 result chain")
        if observation.eligible:
            if (
                report is None
                or report.schema_version != "autocontext.kernelbench-eval/v4"
                or observation.derived_statistics_receipt is None
            ):
                raise RuntimeError("H100 profile evidence requires a complete v4 result chain")
        elif observation.derived_statistics_receipt is not None or (
            report is not None and report.schema_version != "autocontext.kernelbench-eval/v4"
        ):
            raise RuntimeError("H100 profile evidence requires a complete v4 result chain")
        confirmation = attempt.confirmation_observation
        if confirmation is not None:
            confirmation_report = confirmation.report
            if confirmation_report is None or confirmation.protocol_id is None:
                raise RuntimeError("H100 profile evidence requires report-backed confirmation identity")
            if confirmation.eligible:
                if (
                    confirmation_report.schema_version != "autocontext.kernelbench-eval/v4"
                    or confirmation.derived_statistics_receipt is None
                ):
                    raise RuntimeError("H100 profile evidence requires a complete v4 result chain")
            elif confirmation.derived_statistics_receipt is not None or (
                confirmation_report is not None
                and confirmation_report.schema_version != "autocontext.kernelbench-eval/v4"
            ):
                raise RuntimeError("H100 profile evidence requires a complete v4 result chain")


def _verify_v4_profile_policy_receipts(profile: dict[str, Any]) -> None:
    """Reproduce every canonical policy/calibration/statistics identity in a profile."""
    try:
        if profile.get("schema_version") != "autocontext.kernel-h100-profile-evidence/v4":
            raise ValueError("unsupported H100 profile evidence schema")
        if profile.get("evidence_family_version") != "autocontext.kernel-evidence-family/v4":
            raise ValueError("unsupported kernel evidence family")
        policy = KernelDecisionPolicy.model_validate(profile.get("decision_policy"))
        if profile.get("decision_policy_id") != policy.policy_id:
            raise ValueError("decision policy digest does not reproduce")
        calibration = KernelCalibrationReport.model_validate(profile.get("calibration_report"))
        if profile.get("calibration_report_id") != calibration.report_id:
            raise ValueError("calibration report digest does not reproduce")
        if calibration.decision_policy_id != policy.policy_id:
            raise ValueError("calibration report is bound to a different decision policy")
        sequential = policy.sequential_testing
        if sequential is None or profile.get("proposal_budget") != sequential.proposal_cap:
            raise ValueError("profile proposal budget disagrees with its decision policy")
        proposals_evaluated = profile.get("proposals_evaluated")
        promotions = profile.get("promotions")
        if not isinstance(proposals_evaluated, int) or not 0 <= proposals_evaluated <= sequential.proposal_cap:
            raise ValueError("profile proposal count is outside its decision-policy budget")
        if not isinstance(promotions, int) or not 0 <= promotions <= proposals_evaluated:
            raise ValueError("profile promotion count is inconsistent")
        for name in ("primary_receipt", "confirmation_receipt"):
            wrapper = profile.get(name)
            if wrapper is None:
                if name == "primary_receipt" or (name == "confirmation_receipt" and promotions > 0):
                    raise ValueError(f"{name} is required by the profile disposition")
                continue
            if not isinstance(wrapper, dict):
                raise ValueError(f"{name} must be an object")
            receipt = KernelDerivedStatisticsReceipt.model_validate(wrapper.get("derived_statistics_receipt"))
            if wrapper.get("derived_statistics_receipt_id") != receipt.receipt_id:
                raise ValueError(f"{name} derived statistics digest does not reproduce")
            if receipt.statistics_policy_id != policy.statistics.policy_id:
                raise ValueError(f"{name} statistics receipt is bound to a different policy")
            if receipt.raw_report_digest != wrapper.get("report_digest"):
                raise ValueError(f"{name} statistics receipt is bound to a different report")
    except (TypeError, ValueError) as exc:
        raise RuntimeError("H100 profile policy receipts failed canonical replay") from exc


def _verified_h100_attestation(report: Any, runtime: H100DockerRuntimeConfig) -> dict[str, Any]:
    """Bind a canonical H100 identity to the exact trusted host attestation."""
    hardware = report.hardware
    if (
        hardware.backend.casefold() != "cuda"
        or hardware.architecture.casefold() != "sm90"
        or "h100" not in hardware.device_name.casefold()
    ):
        raise RuntimeError("the campaign receipt is not bound to a CUDA SM90 NVIDIA H100")
    metadata = hardware.metadata
    required = {
        "device_id": metadata.get("device_grant"),
        "isolation_kind": metadata.get("device_isolation_kind"),
        "enforced_memory_bytes": metadata.get("device_enforced_memory_bytes"),
        "attestor_id": metadata.get("device_attestor_id"),
        "digest": metadata.get("device_attestation_digest"),
    }
    if any(not isinstance(value, str) or not value for value in required.values()):
        raise RuntimeError("the champion receipt is missing a complete GPU partition attestation")
    capacity = str(required["enforced_memory_bytes"])
    if not capacity.isascii() or not capacity.isdecimal() or str(int(capacity)) != capacity:
        raise RuntimeError("the champion receipt GPU attestation capacity is not canonical")
    payload = {
        "device_id": str(required["device_id"]),
        "isolation_kind": str(required["isolation_kind"]),
        "enforced_memory_bytes": int(capacity),
        "attestor_id": str(required["attestor_id"]),
    }
    expected_runtime = {
        "device_id": runtime.gpu_device,
        "isolation_kind": runtime.gpu_isolation_kind,
        "enforced_memory_bytes": runtime.gpu_memory_bytes,
    }
    if any(payload[key] != value for key, value in expected_runtime.items()):
        raise RuntimeError("the champion receipt GPU partition does not match the host-owned runtime grant")
    expected_digest = content_digest(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    if required["digest"] != expected_digest:
        raise RuntimeError("the champion receipt GPU attestation digest does not match its exact payload")
    return {**payload, "digest": expected_digest}


def _verified_profile_authority_receipt(
    report: Any,
    *,
    runtime: H100DockerRuntimeConfig,
    expected_identity: dict[str, str],
) -> Any:
    """Authenticate one exported report against its host-pinned evaluator identity."""

    receipt = getattr(report, "evaluator_authority_receipt", None)
    if receipt is None:
        raise RuntimeError("H100 profile evidence is missing a trusted-evaluator authority receipt")
    if set(expected_identity) != {"evaluator_build_digest", "boundary_manifest_digest"}:
        raise RuntimeError("H100 profile evidence is missing its host-computed authority identity")
    try:
        report_payload = report.model_dump(mode="json")
        verify_authority_receipt(
            receipt,
            report_payload,
            trusted_key_id=runtime.authority_hmac_key_id,
            trusted_secret=read_authority_hmac_secret(runtime.authority_hmac_secret_path),
            expected_evaluator_build_digest=expected_identity["evaluator_build_digest"],
            expected_boundary_manifest_digest=expected_identity["boundary_manifest_digest"],
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError("H100 profile authority receipt failed authenticated replay") from exc
    return receipt


def _build_profile_evidence(
    *,
    result: KernelEvolutionResult,
    run_id: str,
    precision_profile: str,
    primary_commitment: str,
    confirmation_commitments: tuple[str, ...],
    runtime: H100DockerRuntimeConfig,
    authority_identities: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Build and validate the portable H100 profile receipt without side effects."""
    if result.run_id != run_id or result.problem_id != PROBLEM_ID:
        raise RuntimeError("the campaign result identity does not match the requested run")
    if result.precision_profile != precision_profile:
        raise RuntimeError("the campaign result precision profile does not match the requested profile")
    decision_policy = result.decision_policy
    if decision_policy is None:
        raise RuntimeError("the campaign result is missing its immutable decision policy")
    expected_policy = KernelDecisionPolicy(
        schema_version="autocontext.kernel-decision-policy/v2",
        evidence_family_version="autocontext.kernel-evidence-family/v4",
        statistics=KernelStatisticsPolicy(
            schema_version="autocontext.kernel-statistics-policy/v2",
            method="paired-sign-eprocess/v1",
            bootstrap_samples=None,
            seed_derivation="sha256-plan-commitment-block-schedule/v1",
            min_timing_blocks=8,
            require_resource_telemetry=True,
            max_gpu_memory_bytes=runtime.gpu_memory_bytes,
            block_definition="balanced-interleaved-paired-block/v1",
            dependence_assumption="conditional-threshold-win-probability-lte-half/v1",
            null_win_probability=0.5,
            betting_fraction=1.0,
            improvement_margin=0.05,
        ),
        require_confirmation=True,
        min_relative_improvement=0.05,
        require_confidence=True,
        max_p95_regression=0.05,
        max_environment_drift=0.10,
        max_peak_memory_fraction=0.80,
        target_reference_speedup=2.0,
        sequential_testing=KernelSequentialTestingPolicy(
            proposal_cap=MAX_PROPOSALS,
            familywise_alpha=0.05,
        ),
    )
    if decision_policy != expected_policy:
        raise RuntimeError("the campaign result decision policy does not match the canonical H100 profile")
    _require_complete_v4_result_chain(result)
    decision_policy_id = decision_policy.policy_id
    calibration = calibrate_kernel_promotion(decision_policy)
    policy_digest = decision_policy_id.removeprefix("sha256:")
    if (
        not decision_policy_id.startswith("sha256:")
        or len(policy_digest) != 64
        or any(character not in "0123456789abcdef" for character in policy_digest)
    ):
        raise RuntimeError("the campaign result contains an invalid decision policy digest")

    promoted = [attempt for attempt in result.attempts if attempt.decision == "promoted"]
    champion = next((attempt for attempt in result.attempts if attempt.attempt_id == result.champion_attempt_id), None)
    if champion is None:
        raise RuntimeError("the exact champion attempt is absent from the campaign result")
    champion_report = champion.observation.report
    primary_statistics = champion.observation.derived_statistics_receipt
    if champion_report is None or champion.report_digest is None or primary_statistics is None:
        raise RuntimeError("the exact champion is missing its primary benchmark receipt")
    if champion_report.protocol.seed_commitment != primary_commitment:
        raise RuntimeError("the champion primary receipt is not bound to the configured primary plan")

    hardware_attestation = _verified_h100_attestation(champion_report, runtime)
    primary_authority_receipt = _verified_profile_authority_receipt(
        champion_report,
        runtime=runtime,
        expected_identity=authority_identities.get(primary_commitment, {}),
    )
    primary_accelerator = primary_authority_receipt.accelerator_attestation
    if (
        primary_accelerator.device_id != hardware_attestation["device_id"]
        or primary_accelerator.isolation_kind != hardware_attestation["isolation_kind"]
        or primary_accelerator.enforced_memory_bytes != hardware_attestation["enforced_memory_bytes"]
        or primary_accelerator.attestor_id != hardware_attestation["attestor_id"]
        or primary_accelerator.metadata.get("grant_attestation_digest") != hardware_attestation["digest"]
    ):
        raise RuntimeError("the champion authority receipt changed the host-attested accelerator identity")

    primary_receipt = {
        "report_digest": champion.report_digest,
        "protocol_id": champion.protocol_id,
        "protocol_compatibility_id": champion.protocol_compatibility_id,
        "plan_commitment": champion_report.protocol.seed_commitment,
        "hardware_scope_id": champion.hardware_scope_id,
        "baseline_id": champion.baseline_id,
        "authority_receipt_digest": primary_authority_receipt.receipt_digest,
        "authority_receipt": primary_authority_receipt.model_dump(mode="json"),
        "derived_statistics_receipt_id": primary_statistics.receipt_id,
        "derived_statistics_receipt": primary_statistics.model_dump(mode="json"),
    }
    confirmation_receipt = None
    if champion.role == "candidate" and champion.decision == "promoted":
        confirmation = champion.confirmation_observation
        confirmation_report = confirmation.report if confirmation is not None else None
        if (
            confirmation is None
            or confirmation_report is None
            or champion.confirmation_report_digest is None
            or champion.confirmation_decision is None
            or not champion.confirmation_decision.promote
            or confirmation.derived_statistics_receipt is None
        ):
            raise RuntimeError("a promoted champion is missing its successful confirmation receipt")
        confirmation_commitment = confirmation_report.protocol.seed_commitment
        confirmation_statistics = confirmation.derived_statistics_receipt
        assert confirmation_statistics is not None
        if confirmation_commitment not in confirmation_commitments:
            raise RuntimeError("the champion confirmation receipt is not bound to an approved confirmation plan")
        confirmation_attestation = _verified_h100_attestation(confirmation_report, runtime)
        if confirmation_attestation != hardware_attestation:
            raise RuntimeError("the champion confirmation receipt changed the attested GPU partition")
        confirmation_authority_receipt = _verified_profile_authority_receipt(
            confirmation_report,
            runtime=runtime,
            expected_identity=authority_identities.get(confirmation_commitment, {}),
        )
        confirmation_accelerator = confirmation_authority_receipt.accelerator_attestation
        if (
            confirmation_accelerator.device_id != confirmation_attestation["device_id"]
            or confirmation_accelerator.isolation_kind != confirmation_attestation["isolation_kind"]
            or confirmation_accelerator.enforced_memory_bytes != confirmation_attestation["enforced_memory_bytes"]
            or confirmation_accelerator.attestor_id != confirmation_attestation["attestor_id"]
            or confirmation_accelerator.metadata.get("grant_attestation_digest") != confirmation_attestation["digest"]
        ):
            raise RuntimeError("the confirmation authority receipt changed the host-attested accelerator identity")
        confirmation_receipt = {
            "report_digest": champion.confirmation_report_digest,
            "protocol_id": confirmation.protocol_id,
            "protocol_compatibility_id": confirmation.protocol_compatibility_id,
            "plan_commitment": confirmation_commitment,
            "hardware_scope_id": confirmation.hardware_scope_id,
            "baseline_id": confirmation.baseline_id,
            "authority_receipt_digest": confirmation_authority_receipt.receipt_digest,
            "authority_receipt": confirmation_authority_receipt.model_dump(mode="json"),
            "derived_statistics_receipt_id": confirmation_statistics.receipt_id,
            "derived_statistics_receipt": confirmation_statistics.model_dump(mode="json"),
        }

    holdout_correctness = (
        [item.model_dump(mode="json") for item in champion_report.correctness.slices if item.split == "holdout"]
        if champion_report.correctness is not None
        else []
    )
    holdout_performance = (
        [item.model_dump(mode="json") for item in champion_report.performance.cases if item.split == "holdout"]
        if champion_report.performance is not None
        else []
    )
    profile = {
        "schema_version": "autocontext.kernel-h100-profile-evidence/v4",
        "evidence_status": "observed_live_run",
        "run_id": run_id,
        "precision_profile": precision_profile,
        "champion": {
            "attempt_id": champion.attempt_id,
            "artifact_identity_version": champion.artifact_identity_version,
            "artifact_digest": champion.artifact_digest,
            "source_digest": champion.source_digest,
            "source_suffix": champion.source_suffix,
            "entrypoint": champion.entrypoint,
        },
        "primary_receipt": primary_receipt,
        "confirmation_receipt": confirmation_receipt,
        "hardware_attestation": hardware_attestation,
        "evidence_family_version": "autocontext.kernel-evidence-family/v4",
        "decision_policy_id": decision_policy_id,
        "decision_policy": decision_policy.model_dump(mode="json"),
        "calibration_report_id": calibration.report_id,
        "calibration_report": calibration.model_dump(mode="json"),
        "protocol_id": result.protocol_id,
        "protocol_compatibility_id": result.protocol_compatibility_id,
        "primary_private_plan_commitment": primary_commitment,
        "confirmation_private_plan_commitments": list(confirmation_commitments),
        "proposal_budget": MAX_PROPOSALS,
        "proposals_evaluated": len(result.attempts) - 1,
        "promotions": len(promoted),
        "improvement_survived_profile": bool(promoted),
        "improvement_survived_strict_fp32": bool(promoted) if precision_profile == STRICT_PROFILE else None,
        "all_holdout_correctness_passed": bool(holdout_correctness) and all(item["passed"] for item in holdout_correctness),
        "all_holdout_no_regression_passed": bool(holdout_performance)
        and all(item["passed_no_regression"] for item in holdout_performance),
        "holdout_correctness": holdout_correctness,
        "holdout_performance": holdout_performance,
        "sequential_evidence": [
            attempt.sequential_evidence.model_dump(mode="json")
            for attempt in result.attempts
            if attempt.sequential_evidence is not None
        ],
    }
    _verify_v4_profile_policy_receipts(profile)
    signing_secret = read_authority_hmac_secret(runtime.authority_hmac_secret_path)
    envelope = build_profile_evidence_envelope(
        profile,
        signing_key_id=runtime.authority_hmac_key_id,
        signing_secret=signing_secret,
    )
    verified = verify_profile_evidence_envelope(
        envelope,
        trusted_key_id=runtime.authority_hmac_key_id,
        trusted_secret=signing_secret,
    )
    _verify_v4_profile_policy_receipts(verified.profile)
    payload: dict[str, Any] = envelope.model_dump(mode="json")
    return payload


def _install_sigterm_interrupt() -> None:
    def interrupt(_signum: int, _frame: FrameType | None) -> None:
        raise KeyboardInterrupt("received SIGTERM")

    signal.signal(signal.SIGTERM, interrupt)


def main() -> None:
    bundle = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--autokernel-root", type=Path, required=True)
    parser.add_argument(
        "--baseline",
        type=Path,
        help="Initial incumbent source (defaults to <autokernel-root>/kernel.py)",
    )
    parser.add_argument("--worker-image", required=True, help="Digest-pinned CUDA/Triton worker image")
    parser.add_argument("--docker-binary", default="docker")
    parser.add_argument("--nvidia-smi-binary", default="nvidia-smi")
    parser.add_argument("--container-python", required=True, help="Python executable inside the pinned worker image")
    parser.add_argument("--gpu-device", required=True, help="Explicit MIG UUID reported by the trusted host plane")
    parser.add_argument(
        "--gpu-isolation-kind",
        choices=("mig",),
        required=True,
        help="Host-attested isolation type; this composition currently supports explicit MIG UUIDs only",
    )
    parser.add_argument("--gpu-memory-bytes", type=_positive_int, required=True)
    parser.add_argument("--authority-hmac-key-id", required=True, help="Operator-pinned evaluator signing key id")
    parser.add_argument(
        "--authority-hmac-secret-file",
        type=Path,
        required=True,
        help="Owner-only host file mounted only into the trusted evaluator",
    )
    parser.add_argument("--worker-memory-mb", type=_positive_int, default=16_384)
    parser.add_argument("--worker-cpu-count", type=_positive_float, default=8.0)
    parser.add_argument("--worker-cpu-time-seconds", type=_positive_int, default=600)
    parser.add_argument("--worker-pids-limit", type=_positive_int, default=128)
    parser.add_argument("--max-output-bytes", type=_positive_int, default=64_000)
    parser.add_argument("--max-report-bytes", type=_positive_int, default=2_000_000)
    parser.add_argument("--max-report-entries", type=_positive_int, default=1_024)
    parser.add_argument("--max-report-depth", type=_positive_int, default=16)
    parser.add_argument("--max-workspace-bytes", type=_positive_int, default=512 * 1024 * 1024)
    parser.add_argument("--max-workspace-inodes", type=_positive_int, default=8_192)
    parser.add_argument("--benchmark-timeout", type=_positive_float, default=240.0)
    parser.add_argument("--mailbox", type=Path, required=True)
    parser.add_argument(
        "--sealed-audit-root",
        type=Path,
        required=True,
        help="Operator-only root not mounted into or disclosed through the adaptive mailbox",
    )
    parser.add_argument("--precision-profile", choices=PROFILE_NAMES, required=True)
    parser.add_argument("--primary-private-plan", type=Path, required=True)
    parser.add_argument(
        "--confirmation-private-plan",
        type=Path,
        action="append",
        required=True,
        help="Fresh confirmation plan; repeat at least once per requested proposal",
    )
    parser.add_argument("--proposals", type=_bounded_proposals, default=3)
    parser.add_argument(
        "--generator",
        choices=("provider", "mailbox"),
        default="mailbox",
        help="Use a provider-registry model or the operator mailbox fallback.",
    )
    parser.add_argument(
        "--generation-provider",
        default="anthropic",
        help="Provider-registry transport used when --generator=provider.",
    )
    parser.add_argument("--generation-model", help="Provider model override.")
    parser.add_argument("--generation-base-url", help="OpenAI-compatible provider base URL.")
    parser.add_argument(
        "--generation-max-retries",
        type=_non_negative_int,
        default=2,
        help="Durably accounted retries per proposal.",
    )
    parser.add_argument("--generation-max-output-tokens", type=_positive_int, default=8_192)
    parser.add_argument("--generation-max-input-tokens-total", type=_positive_int, default=200_000)
    parser.add_argument("--generation-max-output-tokens-total", type=_positive_int, default=100_000)
    parser.add_argument("--generation-max-tokens-total", type=_positive_int, default=300_000)
    parser.add_argument("--generation-max-cost-usd", type=_positive_float, default=100.0)
    parser.add_argument("--generation-max-wall-seconds", type=_positive_float, default=86_400.0)
    parser.add_argument("--candidate-wait-timeout", type=_bounded_timeout, default=3_600.0)
    parser.add_argument("--output", type=Path, default=Path("runs/kernel-evolution-h100"))
    parser.add_argument("--run-id")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume the exact run ID after verifying its durable lineage and generation receipts.",
    )
    args = parser.parse_args()

    if args.resume and not args.run_id:
        parser.error("--resume requires --run-id")

    profile = PROFILES[args.precision_profile]
    primary_plan_path = args.primary_private_plan.resolve()
    confirmation_plan_paths = tuple(path.resolve() for path in args.confirmation_private_plan)
    primary_commitment = private_plan_commitment(primary_plan_path)
    confirmation_commitments = tuple(private_plan_commitment(path) for path in confirmation_plan_paths)
    primary_plan = load_private_plan(
        primary_plan_path,
        profile_name=profile.name,
        role="primary",
        expected_commitment=primary_commitment,
    )
    confirmation_plans = tuple(
        load_private_plan(
            path,
            profile_name=profile.name,
            role="confirmation",
            expected_commitment=commitment,
        )
        for path, commitment in zip(confirmation_plan_paths, confirmation_commitments, strict=True)
    )
    try:
        _validate_confirmation_schedule(primary_plan, confirmation_plans, proposals=args.proposals)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    autokernel_root = args.autokernel_root.resolve()
    baseline_path = (args.baseline or (autokernel_root / "kernel.py")).resolve()
    primary_adapter = bundle / "adapter.py"
    confirmation_adapter = bundle / "confirmation_adapter.py"
    reference = bundle / "reference.py"
    required = [
        baseline_path,
        primary_adapter,
        confirmation_adapter,
        reference,
        primary_plan_path,
        *confirmation_plan_paths,
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"required files do not exist: {', '.join(missing)}")

    limits = DockerKernelWorkerLimits(
        memory_mb=args.worker_memory_mb,
        cpu_count=args.worker_cpu_count,
        cpu_time_seconds=args.worker_cpu_time_seconds,
        pids_limit=args.worker_pids_limit,
        max_output_bytes=args.max_output_bytes,
        max_report_bytes=args.max_report_bytes,
        max_report_entries=args.max_report_entries,
        max_report_depth=args.max_report_depth,
        max_workspace_bytes=args.max_workspace_bytes,
        max_workspace_inodes=args.max_workspace_inodes,
        max_gpu_memory_bytes=args.gpu_memory_bytes,
    )
    runtime = H100DockerRuntimeConfig(
        image=args.worker_image,
        docker_binary=args.docker_binary,
        nvidia_smi_binary=args.nvidia_smi_binary,
        container_python=args.container_python,
        gpu_device=args.gpu_device,
        gpu_isolation_kind=args.gpu_isolation_kind,
        gpu_memory_bytes=args.gpu_memory_bytes,
        authority_hmac_key_id=args.authority_hmac_key_id,
        authority_hmac_secret_path=args.authority_hmac_secret_file.resolve(strict=True),
        limits=limits,
        timeout_seconds=args.benchmark_timeout,
    )

    # No library-level construction bypass exists: the composition helper also
    # enforces this boundary. Keep the explicit CLI preflight before evaluator,
    # mailbox, or GPU-side work for a clear operator error.
    try:
        require_protected_evaluator_boundary()
    except ProductionEvaluatorBoundaryUnavailable as exc:
        raise SystemExit(str(exc)) from exc

    run_id = args.run_id or f"kernel_h100_{profile.name}_{uuid.uuid4().hex}"
    output_namespace = profile_output_root(args.output, profile.name)
    mailbox = args.mailbox.resolve()
    output_root = args.output.resolve()
    sealed_audit_root = args.sealed_audit_root.resolve()
    public_roots = (mailbox, output_root)
    if any(
        sealed_audit_root == public or sealed_audit_root.is_relative_to(public) or public.is_relative_to(sealed_audit_root)
        for public in public_roots
    ):
        raise SystemExit("sealed audit root must be disjoint from mailbox and public output roots")

    primary_evaluator = _make_evaluator(
        runtime=runtime,
        bundle=bundle,
        adapter=primary_adapter,
        autokernel_root=autokernel_root,
        precision_profile=profile.name,
        private_plan=primary_plan_path,
        plan_commitment=primary_commitment,
        proposal_cap=MAX_PROPOSALS,
        familywise_alpha=0.05,
    )
    confirmation_evaluators = tuple(
        _make_evaluator(
            runtime=runtime,
            bundle=bundle,
            adapter=confirmation_adapter,
            autokernel_root=autokernel_root,
            precision_profile=profile.name,
            private_plan=path,
            plan_commitment=commitment,
            proposal_cap=MAX_PROPOSALS,
            familywise_alpha=0.05,
        )
        for path, commitment in zip(confirmation_plan_paths, confirmation_commitments, strict=True)
    )
    authority_identities = {
        commitment: {
            "evaluator_build_digest": evaluator.config.expected_evaluator_build_digest,
            "boundary_manifest_digest": evaluator.config.expected_boundary_manifest_digest,
        }
        for commitment, evaluator in (
            (primary_commitment, primary_evaluator),
            *zip(confirmation_commitments, confirmation_evaluators, strict=True),
        )
    }
    if any(not all(isinstance(value, str) for value in identity.values()) for identity in authority_identities.values()):
        raise RuntimeError("protected evaluator omitted its host-computed authority identity")

    mailbox.mkdir(parents=True, exist_ok=True)
    if not args.resume and any(mailbox.iterdir()):
        raise SystemExit(f"mailbox must be a new or empty directory: {mailbox}")

    confirmation_cursor: int | None = None

    def confirm(candidate: KernelCandidate, incumbent: KernelCandidate) -> KernelBenchmarkObservation | None:
        nonlocal confirmation_cursor
        if confirmation_cursor is None:
            confirmation_cursor = sum(
                attempt.role == "candidate" and attempt.confirmation_observation is not None
                for attempt in runner.attempts
            )
        if confirmation_cursor >= len(confirmation_evaluators):
            raise RuntimeError("confirmation plan schedule is exhausted")
        confirmation_evaluator = confirmation_evaluators[confirmation_cursor]
        confirmation_cursor += 1
        fresh_baseline = confirmation_evaluator.evaluate(incumbent, incumbent)
        if not fresh_baseline.eligible:
            return None
        return confirmation_evaluator.evaluate(
            candidate,
            incumbent,
            expected_scope_id=fresh_baseline.hardware_scope_id,
            expected_baseline_id=fresh_baseline.baseline_id,
            expected_protocol_id=fresh_baseline.protocol_id,
        )

    generation_budget = KernelGenerationBudget(
        proposal_cap=MAX_PROPOSALS,
        max_retries_per_proposal=args.generation_max_retries,
        max_output_tokens_per_call=args.generation_max_output_tokens,
        max_total_input_tokens=args.generation_max_input_tokens_total,
        max_total_output_tokens=args.generation_max_output_tokens_total,
        max_total_tokens=args.generation_max_tokens_total,
        max_cost_usd=args.generation_max_cost_usd,
        max_wall_seconds=args.generation_max_wall_seconds,
        max_source_bytes=MAX_CANDIDATE_BYTES,
    )
    if args.generator == "provider":
        provider = create_provider(
            args.generation_provider,
            base_url=args.generation_base_url,
            model=args.generation_model,
            max_retries=0,
        )
        generator = ProviderKernelGenerator(
            provider,
            provider_id=args.generation_provider,
            model=args.generation_model,
            budget=generation_budget,
            transport_identity=content_digest(
                json.dumps(
                    {
                        "provider": args.generation_provider,
                        "base_url": args.generation_base_url or "registry-default",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ),
            source_suffix=".py",
            entrypoint="kernel_fn",
            cancellation_requested=lambda: (output_namespace / run_id / "control" / "stop.json").is_file(),
        )
    else:
        generator = MailboxGenerator(
            mailbox,
            timeout_seconds=args.candidate_wait_timeout,
            cancellation_requested=lambda: (output_namespace / run_id / "control" / "stop.json").is_file(),
        )
    runner = KernelEvolutionRunner(
        KernelEvolutionConfig(
            problem_id=PROBLEM_ID,
            task_prompt=(
                f"Optimize the complete Python/Triton kernel_fn(a, b) module under the host-owned {profile.name} "
                "matrix-multiplication profile on NVIDIA H100 SM90. Preserve all shapes, layouts, signs, magnitudes, "
                "cancellation behavior, dtype, and inputs. Return only "
                "the complete executable Python source without Markdown fences. The immutable benchmark's correctness "
                "and performance feedback is authoritative."
            ),
            baseline_source=baseline_path.read_text(encoding="utf-8"),
            source_suffix=".py",
            entrypoint="kernel_fn",
            min_relative_improvement=0.05,
            require_confidence=True,
            max_p95_regression=0.05,
            max_environment_drift=0.10,
            max_peak_memory_fraction=0.80,
            target_reference_speedup=2.0,
            precision_profile=profile.name,
            proposal_cap=MAX_PROPOSALS,
            familywise_alpha=0.05,
        ),
        generator,
        primary_evaluator,
        output_namespace,
        run_id=run_id,
        confirmation_fn=confirm,
        sealed_audit_root=sealed_audit_root,
        generation_budget=generation_budget,
        resume=args.resume,
    )
    baseline = _candidate(baseline_path.read_text(encoding="utf-8"))
    campaign_config = {
        "schema_version": "autocontext.kernel-campaign/v3",
        "run_id": run_id,
        "problem_id": PROBLEM_ID,
        "precision_profile": profile.name,
        "profile_output_namespace": str(output_namespace),
        "proposals_requested": args.proposals,
        "hard_proposal_cap": MAX_PROPOSALS,
        "sequential_testing": {
            "method": "bonferroni",
            "familywise_alpha": 0.05,
            "per_proposal_alpha": 0.05 / MAX_PROPOSALS,
        },
        "primary_private_plan_commitment": primary_commitment,
        "confirmation_private_plan_commitments": list(confirmation_commitments),
        "candidate_wait_timeout_seconds": args.candidate_wait_timeout,
        "generator": {
            "kind": args.generator,
            "provider": args.generation_provider if args.generator == "provider" else "mailbox",
            "model": getattr(generator, "model", None),
            "generation_budget_id": generation_budget.budget_id,
            "generation_budget": generation_budget.model_dump(mode="json"),
        },
        "resume": args.resume,
        "baseline_path": str(baseline_path),
        "artifact_identity_version": baseline.artifact_identity_version,
        "baseline_artifact_digest": baseline.artifact_digest,
        "baseline_source_digest": baseline.source_digest,
        "mailbox": str(mailbox),
        "sealed_confirmation_audit": {
            "schema_version": "autocontext.kernel-sealed-audit-boundary/v1",
            "available_to_adaptive_generator": False,
            "published_after_terminal": True,
        },
        "docker_runtime": runtime.manifest(),
        "primary_benchmark": primary_evaluator.manifest(),
        "confirmation_benchmarks": [evaluator.manifest() for evaluator in confirmation_evaluators],
    }
    _atomic_json(runner.run_dir / "campaign_config.json", campaign_config)
    _atomic_json(mailbox / "campaign_config.json", campaign_config)
    _atomic_json(mailbox / "campaign_status.json", {**campaign_config, "status": "running"})
    _install_sigterm_interrupt()

    try:
        result = runner.run(proposals=args.proposals)
        profile_evidence = _build_profile_evidence(
            result=result,
            run_id=run_id,
            precision_profile=profile.name,
            primary_commitment=primary_commitment,
            confirmation_commitments=confirmation_commitments,
            runtime=runtime,
            authority_identities=authority_identities,
        )
        status = {
            **campaign_config,
            "status": "complete",
            "champion_artifact_digest": result.champion_artifact_digest,
            "champion_source_digest": result.champion_source_digest,
            "champion_speedup_vs_reference": result.champion_speedup_vs_reference,
            **_progress(runner.run_dir),
        }
        _atomic_json(runner.run_dir / "profile_evidence.json", profile_evidence)
        _atomic_json(mailbox / "profile_evidence.json", profile_evidence)
        read_kernel_campaign_status(output_namespace, run_id)
        # Publish `complete` only after all required evidence is validated and
        # durably exported. Any failure above is reported by the handler below.
        _atomic_json(mailbox / "campaign_status.json", status)
    except KernelGenerationCancelled as exc:
        status = {
            **campaign_config,
            "status": "cancelled",
            "reason": str(exc)[:1_000],
            **_best_effort_progress(runner.run_dir),
        }
        _atomic_json(mailbox / "campaign_status.json", status)
        print(json.dumps(status, indent=2, sort_keys=True), flush=True)
        raise SystemExit(0) from None
    except KeyboardInterrupt:
        status = {**campaign_config, "status": "interrupted", **_best_effort_progress(runner.run_dir)}
        _atomic_json(mailbox / "campaign_status.json", status)
        print(json.dumps(status, indent=2, sort_keys=True), file=sys.stderr, flush=True)
        raise SystemExit(130) from None
    except Exception as exc:
        status = {
            **campaign_config,
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc)[:1_000],
            **_best_effort_progress(runner.run_dir),
        }
        _atomic_json(mailbox / "campaign_status.json", status)
        raise
    print(json.dumps(status, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
