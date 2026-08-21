#!/usr/bin/env python3
"""Run a bounded, mailbox-driven H100 kernel evolution campaign.

From the ``autocontext/`` Python package directory, see ``README.md`` for the
full command. The generator writes AutoContext's exact recursive prompt to a
mailbox, prints it for detached logs, and waits for an operator-authored
``candidate_N.py`` response, numbered from zero.
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
from pathlib import Path
from types import FrameType
from typing import Any

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
    ExternalKernelBenchmarkRunner,
    KernelBenchmarkEvaluator,
    KernelBenchmarkEvaluatorConfig,
    KernelBenchmarkObservation,
    KernelCandidate,
    KernelEvolutionConfig,
    KernelEvolutionRunner,
)

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


class VerbatimSource(str):
    """Keep byte-addressed source intact through generic text normalization."""

    def strip(self, chars: str | None = None) -> str:
        if chars is None:
            return self
        return super().strip(chars)


class MailboxGenerator:
    """Persist each recursive prompt and wait for one stable source file."""

    def __init__(self, mailbox: Path, *, timeout_seconds: float) -> None:
        self._mailbox = mailbox
        self._timeout_seconds = timeout_seconds

    def __call__(self, prompt: str, generation: int) -> str:
        number = generation
        prompt_path = self._mailbox / f"prompt_{number}.md"
        candidate_path = self._mailbox / f"candidate_{number}.py"
        receipt_path = self._mailbox / f"accepted_candidate_{number}.json"
        collisions = [path for path in (prompt_path, candidate_path, receipt_path) if path.exists() or path.is_symlink()]
        if collisions:
            names = ", ".join(path.name for path in collisions)
            raise FileExistsError(f"mailbox contains stale generation {number} files: {names}")

        _atomic_text(prompt_path, prompt)
        print(f"\n===== AUTOCONTEXT KERNEL PROMPT {number} ({prompt_path}) =====", flush=True)
        print(prompt, flush=True)
        print(f"===== END PROMPT {number}; WAITING FOR {candidate_path} =====\n", flush=True)

        deadline = time.monotonic() + self._timeout_seconds
        while True:
            if candidate_path.is_symlink():
                raise ValueError(f"candidate mailbox file must not be a symlink: {candidate_path}")
            if candidate_path.exists():
                source = self._read_stable_candidate(candidate_path, deadline=deadline)
                candidate = _candidate(source)
                _atomic_json(
                    receipt_path,
                    {
                        "schema_version": "autocontext.kernel-mailbox-receipt/v2",
                        "generation": number,
                        "candidate_path": str(candidate_path),
                        "artifact_identity_version": candidate.artifact_identity_version,
                        "artifact_digest": candidate.artifact_digest,
                        "source_digest": candidate.source_digest,
                        "source_bytes": len(candidate.source_bytes),
                    },
                )
                print(f"accepted candidate {number}: {candidate.artifact_digest}", flush=True)
                return VerbatimSource(source)
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
    adapter_python: str,
    adapter: Path,
    shared_adapter: Path,
    reference: Path,
    autokernel_root: Path,
    confirmation: bool,
    precision_profile: str,
    private_plan: Path,
    plan_commitment: str,
    proposal_cap: int,
    familywise_alpha: float,
) -> KernelBenchmarkEvaluator:
    immutable_paths = [adapter, reference]
    if confirmation:
        immutable_paths.append(shared_adapter)
    external = ExternalKernelBenchmarkRunner(
        [
            adapter_python,
            str(adapter),
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
            "--reference",
            str(reference),
            "--report",
            "{report}",
            "--problem-id",
            PROBLEM_ID,
            "--autokernel-root",
            str(autokernel_root),
            "--precision-profile",
            precision_profile,
            "--private-plan",
            str(private_plan),
            "--plan-commitment",
            plan_commitment,
            "--proposal-cap",
            str(proposal_cap),
            "--familywise-alpha",
            str(familywise_alpha),
        ],
        cwd=autokernel_root,
        source_suffix=".py",
        trusted_unsafe=True,
        immutable_paths=[*immutable_paths, adapter.parent / "profile_contract.py", private_plan],
        max_output_bytes=64_000,
        max_report_bytes=2_000_000,
    )
    return KernelBenchmarkEvaluator(
        external,
        KernelBenchmarkEvaluatorConfig(
            problem_id=PROBLEM_ID,
            timeout_seconds=240.0,
            min_timing_blocks=8,
            bootstrap_samples=1_000,
            require_resource_telemetry=True,
        ),
    )


def _progress(run_dir: Path) -> dict[str, int]:
    lineage = run_dir / "lineage.jsonl"
    attempts = len(lineage.read_text(encoding="utf-8").splitlines()) if lineage.exists() else 0
    return {"attempts_persisted": attempts, "proposals_persisted": max(0, attempts - 1)}


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
    parser.add_argument("--adapter-python", type=Path, required=True)
    parser.add_argument("--mailbox", type=Path, required=True)
    parser.add_argument("--precision-profile", choices=PROFILE_NAMES, required=True)
    parser.add_argument("--primary-private-plan", type=Path, required=True)
    parser.add_argument("--confirmation-private-plan", type=Path, required=True)
    parser.add_argument("--proposals", type=_bounded_proposals, default=3)
    parser.add_argument("--candidate-wait-timeout", type=_bounded_timeout, default=3_600.0)
    parser.add_argument("--output", type=Path, default=Path("runs/kernel-evolution-h100"))
    parser.add_argument("--run-id")
    args = parser.parse_args()

    profile = PROFILES[args.precision_profile]
    primary_plan_path = args.primary_private_plan.resolve()
    confirmation_plan_path = args.confirmation_private_plan.resolve()
    primary_commitment = private_plan_commitment(primary_plan_path)
    confirmation_commitment = private_plan_commitment(confirmation_plan_path)
    primary_plan = load_private_plan(
        primary_plan_path,
        profile_name=profile.name,
        role="primary",
        expected_commitment=primary_commitment,
    )
    confirmation_plan = load_private_plan(
        confirmation_plan_path,
        profile_name=profile.name,
        role="confirmation",
        expected_commitment=confirmation_commitment,
    )
    assert_fresh_plans(primary_plan, confirmation_plan)

    autokernel_root = args.autokernel_root.resolve()
    baseline_path = (args.baseline or (autokernel_root / "kernel.py")).resolve()
    primary_adapter = bundle / "adapter.py"
    confirmation_adapter = bundle / "confirmation_adapter.py"
    reference = bundle / "reference.py"
    adapter_python = os.path.abspath(os.fspath(args.adapter_python))
    required = [
        baseline_path,
        primary_adapter,
        confirmation_adapter,
        reference,
        Path(adapter_python),
        primary_plan_path,
        confirmation_plan_path,
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"required files do not exist: {', '.join(missing)}")

    mailbox = args.mailbox.resolve()
    mailbox.mkdir(parents=True, exist_ok=True)
    if any(mailbox.iterdir()):
        raise SystemExit(f"mailbox must be a new or empty directory: {mailbox}")

    primary_evaluator = _make_evaluator(
        adapter_python=adapter_python,
        adapter=primary_adapter,
        shared_adapter=primary_adapter,
        reference=reference,
        autokernel_root=autokernel_root,
        confirmation=False,
        precision_profile=profile.name,
        private_plan=primary_plan_path,
        plan_commitment=primary_commitment,
        proposal_cap=MAX_PROPOSALS,
        familywise_alpha=0.05,
    )
    confirmation_evaluator = _make_evaluator(
        adapter_python=adapter_python,
        adapter=confirmation_adapter,
        shared_adapter=primary_adapter,
        reference=reference,
        autokernel_root=autokernel_root,
        confirmation=True,
        precision_profile=profile.name,
        private_plan=confirmation_plan_path,
        plan_commitment=confirmation_commitment,
        proposal_cap=MAX_PROPOSALS,
        familywise_alpha=0.05,
    )

    def confirm(candidate: KernelCandidate, incumbent: KernelCandidate) -> KernelBenchmarkObservation | None:
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

    run_id = args.run_id or f"kernel_h100_{profile.name}_{uuid.uuid4().hex}"
    generator = MailboxGenerator(mailbox, timeout_seconds=args.candidate_wait_timeout)
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
        profile_output_root(args.output, profile.name),
        run_id=run_id,
        confirmation_fn=confirm,
    )
    baseline = _candidate(baseline_path.read_text(encoding="utf-8"))
    campaign_config = {
        "schema_version": "autocontext.kernel-mailbox-campaign/v2",
        "run_id": run_id,
        "problem_id": PROBLEM_ID,
        "precision_profile": profile.name,
        "profile_output_namespace": str(profile_output_root(args.output, profile.name)),
        "proposals_requested": args.proposals,
        "hard_proposal_cap": MAX_PROPOSALS,
        "sequential_testing": {
            "method": "bonferroni",
            "familywise_alpha": 0.05,
            "per_proposal_alpha": 0.05 / MAX_PROPOSALS,
        },
        "primary_private_plan_commitment": primary_commitment,
        "confirmation_private_plan_commitment": confirmation_commitment,
        "candidate_wait_timeout_seconds": args.candidate_wait_timeout,
        "baseline_path": str(baseline_path),
        "artifact_identity_version": baseline.artifact_identity_version,
        "baseline_artifact_digest": baseline.artifact_digest,
        "baseline_source_digest": baseline.source_digest,
        "mailbox": str(mailbox),
        "run_dir": str(runner.run_dir),
        "primary_benchmark": primary_evaluator.manifest(),
        "confirmation_benchmark": confirmation_evaluator.manifest(),
    }
    _atomic_json(runner.run_dir / "campaign_config.json", campaign_config)
    _atomic_json(mailbox / "campaign_config.json", campaign_config)
    _atomic_json(mailbox / "campaign_status.json", {**campaign_config, "status": "running"})
    _install_sigterm_interrupt()

    try:
        result = runner.run(proposals=args.proposals)
    except KeyboardInterrupt:
        status = {**campaign_config, "status": "interrupted", **_progress(runner.run_dir)}
        _atomic_json(mailbox / "campaign_status.json", status)
        print(json.dumps(status, indent=2, sort_keys=True), file=sys.stderr, flush=True)
        raise SystemExit(130) from None
    except Exception as exc:
        status = {
            **campaign_config,
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc)[:1_000],
            **_progress(runner.run_dir),
        }
        _atomic_json(mailbox / "campaign_status.json", status)
        raise

    status = {
        **campaign_config,
        "status": "complete",
        "champion_artifact_digest": result.champion_artifact_digest,
        "champion_source_digest": result.champion_source_digest,
        "champion_speedup_vs_reference": result.champion_speedup_vs_reference,
        **_progress(runner.run_dir),
    }
    promoted = [attempt for attempt in result.attempts if attempt.decision == "promoted"]
    champion = next(attempt for attempt in result.attempts if attempt.attempt_id == result.champion_attempt_id)
    champion_report = champion.observation.report
    holdout_correctness = (
        [item.model_dump(mode="json") for item in champion_report.correctness.slices if item.split == "holdout"]
        if champion_report is not None and champion_report.correctness is not None
        else []
    )
    holdout_performance = (
        [item.model_dump(mode="json") for item in champion_report.performance.cases if item.split == "holdout"]
        if champion_report is not None and champion_report.performance is not None
        else []
    )
    profile_evidence = {
        "schema_version": "autocontext.kernel-h100-profile-evidence/v1",
        "evidence_status": "observed_live_run",
        "run_id": run_id,
        "precision_profile": profile.name,
        "protocol_id": result.protocol_id,
        "protocol_compatibility_id": result.protocol_compatibility_id,
        "primary_private_plan_commitment": primary_commitment,
        "confirmation_private_plan_commitment": confirmation_commitment,
        "proposal_budget": MAX_PROPOSALS,
        "proposals_evaluated": len(result.attempts) - 1,
        "promotions": len(promoted),
        "improvement_survived_profile": bool(promoted),
        "improvement_survived_strict_fp32": bool(promoted) if profile.name == STRICT_PROFILE else None,
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
    _atomic_json(runner.run_dir / "profile_evidence.json", profile_evidence)
    _atomic_json(mailbox / "profile_evidence.json", profile_evidence)
    _atomic_json(mailbox / "campaign_status.json", status)
    print(json.dumps(status, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
