"""Durable proposal journal, stop control, status, and artifact discovery."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from autocontext.kernel_evolution.campaign_journal_artifacts import (
    add_durable_failure,
    artifact_kind,
    champion_artifact_digest,
)
from autocontext.kernel_evolution.campaign_journal_models import (
    KernelCampaignStatus,
    KernelEvaluationClaim,
    KernelGenerationAttemptLink,
    KernelGenerationCallClaim,
    KernelGenerationClaim,
    KernelGenerationFailureReceipt,
    KernelRunArtifact,
    KernelRunArtifactIndex,
)
from autocontext.kernel_evolution.generation import (
    KernelGenerationBudget,
    KernelGenerationBudgetState,
    KernelGenerationFailure,
    KernelGenerationResult,
)
from autocontext.kernel_evolution.models import KernelCandidate, canonical_digest, content_digest
from autocontext.util.file_lock import advisory_path_lock
from autocontext.util.json_io import read_json, write_json, write_text_atomic

_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")

class KernelCampaignJournalError(RuntimeError):
    """Durable campaign journal is incomplete, conflicting, or ambiguous."""


class KernelCampaignAmbiguousExecution(KernelCampaignJournalError):
    """A dispatch may have occurred and must never be duplicated automatically."""


class KernelCampaignJournal:
    """Append-safe control-plane journal stored beside kernel lineage."""

    def __init__(self, run_dir: Path, run_id: str) -> None:
        if not _SAFE_RUN_ID.fullmatch(run_id) or ".." in run_id:
            raise ValueError("run_id must be a safe path segment")
        self.run_dir = run_dir
        self.run_id = run_id
        self.root = run_dir / "generation"

    def claim_generation(
        self,
        *,
        proposal_index: int,
        prompt: str,
        generator_identity: str,
        system_prompt: str | None = None,
    ) -> KernelGenerationClaim:
        prompt_digest = content_digest(prompt.encode("utf-8"))
        prompt_path = self.run_dir / "prompts" / f"{prompt_digest.removeprefix('sha256:')}.md"
        self._write_exact_text(prompt_path, prompt)
        system_prompt_digest = (
            content_digest(system_prompt.encode("utf-8"))
            if system_prompt is not None
            else None
        )
        if system_prompt is not None and system_prompt_digest is not None:
            system_prompt_path = self.run_dir / "prompts" / (
                f"{system_prompt_digest.removeprefix('sha256:')}.md"
            )
            self._write_exact_text(system_prompt_path, system_prompt)
        claim = KernelGenerationClaim(
            run_id=self.run_id,
            proposal_index=proposal_index,
            prompt_digest=prompt_digest,
            system_prompt_digest=system_prompt_digest,
            generator_identity=generator_identity,
            created_at=datetime.now(UTC).isoformat(),
        )
        with advisory_path_lock(self._dispatch_lock_path()):
            if self.stop_requested():
                from autocontext.kernel_evolution.generation import KernelGenerationCancelled

                raise KernelGenerationCancelled("kernel campaign stop requested before provider dispatch")
            self._write_immutable_json(
                self._generation_dir(proposal_index) / "claim.json",
                claim.model_dump(mode="json"),
            )
        self.refresh_artifact_index()
        return claim

    def claim_generation_call(self, proposal_index: int, call_index: int) -> None:
        """Fence one physical provider call against an operator stop."""
        if self.read_generation_claim(proposal_index) is None:
            raise KernelCampaignJournalError("provider call has no durable proposal claim")
        path = self._generation_call_dir(proposal_index, call_index) / "claim.json"
        with advisory_path_lock(self._dispatch_lock_path()):
            if self.stop_requested():
                from autocontext.kernel_evolution.generation import KernelGenerationCancelled

                raise KernelGenerationCancelled("kernel campaign stop requested before provider dispatch")
            if path.exists():
                claim = KernelGenerationCallClaim.model_validate(read_json(path))
                self._validate_call_claim(claim, proposal_index, call_index)
                return
            claim = KernelGenerationCallClaim(
                run_id=self.run_id,
                proposal_index=proposal_index,
                call_index=call_index,
                created_at=datetime.now(UTC).isoformat(),
            )
            self._write_immutable_json(path, claim.model_dump(mode="json"))
        self.refresh_artifact_index()

    def write_generation_failure(self, failure: KernelGenerationFailure) -> None:
        claim_path = self._generation_call_dir(failure.proposal_index, failure.call_index) / "claim.json"
        if not claim_path.is_file():
            raise KernelCampaignJournalError("provider failure has no durable call claim")
        claim = KernelGenerationCallClaim.model_validate(read_json(claim_path))
        self._validate_call_claim(claim, failure.proposal_index, failure.call_index)
        receipt = KernelGenerationFailureReceipt(
            run_id=self.run_id,
            proposal_index=failure.proposal_index,
            call_index=failure.call_index,
            failure_id=failure.failure_id,
            failure=failure,
        )
        path = self._generation_call_dir(failure.proposal_index, failure.call_index) / "failure.json"
        self._write_immutable_json(path, receipt.model_dump(mode="json"))
        self.refresh_artifact_index()

    def write_generation_result(self, result: KernelGenerationResult) -> Path:
        claim = self.read_generation_claim(result.proposal_index)
        if claim is None:
            raise KernelCampaignJournalError("generation result has no durable pre-dispatch claim")
        if claim.prompt_digest != result.prompt_digest:
            raise KernelCampaignJournalError("generation result prompt digest conflicts with its durable claim")
        if (
            claim.system_prompt_digest is not None
            and claim.system_prompt_digest != result.system_prompt_digest
        ):
            raise KernelCampaignJournalError(
                "generation result system-prompt digest conflicts with its durable claim"
            )
        receipt_path = self.root / "receipts" / f"{result.receipt_id.removeprefix('sha256:')}.json"
        self._write_immutable_json(receipt_path, result.model_dump(mode="json"))
        self._validate_generation_result(result, result.proposal_index, result.receipt_id)
        pointer = self._generation_pointer(result, receipt_path)
        self._write_immutable_json(self._generation_dir(result.proposal_index) / "result.json", pointer)
        self.refresh_artifact_index()
        return receipt_path

    def write_generation_cancellation(
        self,
        proposal_index: int,
        failures: tuple[KernelGenerationFailure, ...],
    ) -> Path:
        """Append a resumable cancellation event without sealing the proposal outcome."""
        if any(item.proposal_index != proposal_index for item in failures):
            raise KernelCampaignJournalError("generation cancellation contains a foreign failure")
        payload = {
            "schema_version": "autocontext.kernel-generation-cancellation/v1",
            "run_id": self.run_id,
            "proposal_index": proposal_index,
            "cancelled_at": datetime.now(UTC).isoformat(),
            "failures": [item.model_dump(mode="json") for item in failures],
        }
        event_id = canonical_digest(payload).removeprefix("sha256:")
        path = self._generation_dir(proposal_index) / "cancellations" / f"{event_id}.json"
        self._write_immutable_json(path, payload)
        self.refresh_artifact_index()
        return path

    def write_terminal_failures(
        self,
        proposal_index: int,
        failures: tuple[KernelGenerationFailure, ...],
        *,
        outcome: str,
    ) -> Path:
        payload = {
            "schema_version": "autocontext.kernel-generation-terminal-failure/v1",
            "run_id": self.run_id,
            "proposal_index": proposal_index,
            "outcome": outcome,
            "failures": [item.model_dump(mode="json") for item in failures],
        }
        path = self._generation_dir(proposal_index) / "failure.json"
        self._write_immutable_json(path, payload)
        self.refresh_artifact_index()
        return path

    @contextmanager
    def begin_evaluation(
        self,
        *,
        generation: int,
        role: Literal["baseline", "candidate"],
        artifact_digest: str,
        generation_receipt_id: str | None = None,
    ) -> Iterator[KernelEvaluationClaim]:
        attempt_id = deterministic_kernel_attempt_id(
            self.run_id,
            generation=generation,
            artifact_digest=artifact_digest,
            generation_receipt_id=generation_receipt_id,
        )
        claim = KernelEvaluationClaim(
            run_id=self.run_id,
            generation=generation,
            role=role,
            generation_receipt_id=generation_receipt_id,
            artifact_digest=artifact_digest,
            attempt_id=attempt_id,
            created_at=datetime.now(UTC).isoformat(),
        )
        with advisory_path_lock(self._dispatch_lock_path()):
            if self.stop_requested():
                from autocontext.kernel_evolution.generation import KernelGenerationCancelled

                raise KernelGenerationCancelled(
                    "kernel campaign stop requested after source generation and before GPU evaluation"
                )
            self._write_immutable_json(
                self._evaluation_claim_path(generation),
                claim.model_dump(mode="json"),
            )
            self.refresh_artifact_index()
            yield claim

    def link_attempt(self, result: KernelGenerationResult, *, attempt_id: str, artifact_digest: str) -> Path:
        expected = deterministic_kernel_attempt_id(
            self.run_id,
            generation=result.proposal_index,
            artifact_digest=artifact_digest,
            generation_receipt_id=result.receipt_id,
        )
        if attempt_id != expected:
            raise KernelCampaignJournalError("attempt id does not match its durable generation claim")
        link = KernelGenerationAttemptLink(
            run_id=self.run_id,
            proposal_index=result.proposal_index,
            generation_receipt_id=result.receipt_id,
            attempt_id=attempt_id,
            artifact_digest=artifact_digest,
        )
        path = self._generation_dir(result.proposal_index) / "attempt-link.json"
        self._write_immutable_json(path, link.model_dump(mode="json"))
        self.refresh_artifact_index()
        return path

    def read_generation_claim(self, proposal_index: int) -> KernelGenerationClaim | None:
        path = self._generation_dir(proposal_index) / "claim.json"
        if not path.exists():
            return None
        claim = KernelGenerationClaim.model_validate(read_json(path))
        if claim.run_id != self.run_id or claim.proposal_index != proposal_index:
            raise KernelCampaignJournalError("generation claim identity is invalid")
        self._validate_prompt_artifact(claim.prompt_digest, label="generation prompt")
        if claim.system_prompt_digest is not None:
            self._validate_prompt_artifact(
                claim.system_prompt_digest,
                label="generation system-prompt",
            )
        return claim

    def read_generation_result(
        self,
        proposal_index: int,
        *,
        recover_orphan: bool = False,
    ) -> KernelGenerationResult | None:
        pointer_path = self._generation_dir(proposal_index) / "result.json"
        if not pointer_path.exists():
            if not recover_orphan:
                return None
            orphan = self._orphan_generation_result(proposal_index)
            if orphan is None:
                return None
            receipt_path, result = orphan
            pointer = self._generation_pointer(result, receipt_path)
            self._write_immutable_json(pointer_path, pointer)
            self.refresh_artifact_index()
            return result
        pointer = read_json(pointer_path)
        if (
            not isinstance(pointer, dict)
            or pointer.get("schema_version") != "autocontext.kernel-generation-pointer/v1"
            or pointer.get("run_id") != self.run_id
            or pointer.get("proposal_index") != proposal_index
        ):
            raise KernelCampaignJournalError("generation result pointer is malformed")
        receipt_id = pointer.get("generation_receipt_id")
        receipt_path_raw = pointer.get("receipt_path")
        if not isinstance(receipt_id, str) or not isinstance(receipt_path_raw, str):
            raise KernelCampaignJournalError("generation result pointer is incomplete")
        expected_receipt_path = f"generation/receipts/{receipt_id.removeprefix('sha256:')}.json"
        if receipt_path_raw != expected_receipt_path:
            raise KernelCampaignJournalError("generation result pointer does not use its content-addressed path")
        receipt_path = self.run_dir / receipt_path_raw
        result = KernelGenerationResult.model_validate(read_json(receipt_path))
        self._validate_generation_result(result, proposal_index, receipt_id)
        return result

    def _validate_generation_result(
        self,
        result: KernelGenerationResult,
        proposal_index: int,
        receipt_id: str,
    ) -> None:
        if result.receipt_id != receipt_id or result.proposal_index != proposal_index:
            raise KernelCampaignJournalError("generation result receipt conflicts with its pointer")
        claim = self.read_generation_claim(proposal_index)
        if claim is None or claim.prompt_digest != result.prompt_digest:
            raise KernelCampaignJournalError("generation result conflicts with its pre-dispatch claim")
        if (
            claim.system_prompt_digest is not None
            and claim.system_prompt_digest != result.system_prompt_digest
        ):
            raise KernelCampaignJournalError("generation result conflicts with its system-prompt claim")
        candidate = KernelCandidate(
            source=result.source,
            source_suffix=result.source_suffix,
            entrypoint=result.entrypoint,
        )
        source_path = self.run_dir / "artifacts" / (
            f"{candidate.artifact_digest.removeprefix('sha256:')}{candidate.source_suffix}"
        )
        if not source_path.is_file() or source_path.read_bytes() != candidate.source_bytes:
            raise KernelCampaignJournalError("generated source artifact is missing or changed")
        failures, unresolved = self.generation_call_state(proposal_index)
        if failures or unresolved:
            expected_failures = tuple(result.failures)
            if failures != expected_failures or unresolved != (result.retry_count + 1,):
                raise KernelCampaignJournalError(
                    "generation call claims do not match the successful receipt"
                )

    def generation_results(self, *, recover_orphans: bool = False) -> list[KernelGenerationResult]:
        results: list[KernelGenerationResult] = []
        proposal_index = 1
        while True:
            result = self.read_generation_result(
                proposal_index,
                recover_orphan=recover_orphans,
            )
            if result is None:
                break
            results.append(result)
            proposal_index += 1
        extra = sorted(self.root.glob("proposals/*/result.json"))
        if len(extra) != len(results):
            raise KernelCampaignJournalError("generation results are not contiguous from proposal one")
        return results

    def generation_call_state(
        self,
        proposal_index: int,
    ) -> tuple[tuple[KernelGenerationFailure, ...], tuple[int, ...]]:
        calls_root = self._generation_dir(proposal_index) / "calls"
        if not calls_root.exists():
            return (), ()
        directories = sorted(item for item in calls_root.iterdir() if item.is_dir())
        expected_names = [f"{index:06d}" for index in range(1, len(directories) + 1)]
        if [item.name for item in directories] != expected_names:
            raise KernelCampaignJournalError("generation call claims are not contiguous from call one")
        failures: list[KernelGenerationFailure] = []
        unresolved: list[int] = []
        for call_index, directory in enumerate(directories, 1):
            claim_path = directory / "claim.json"
            if not claim_path.is_file():
                raise KernelCampaignJournalError("generation call directory has no durable claim")
            claim = KernelGenerationCallClaim.model_validate(read_json(claim_path))
            self._validate_call_claim(claim, proposal_index, call_index)
            failure_path = directory / "failure.json"
            if not failure_path.is_file():
                unresolved.append(call_index)
                continue
            receipt = KernelGenerationFailureReceipt.model_validate(read_json(failure_path))
            if (
                receipt.run_id != self.run_id
                or receipt.proposal_index != proposal_index
                or receipt.call_index != call_index
            ):
                raise KernelCampaignJournalError("generation failure receipt identity is invalid")
            failures.append(receipt.failure)
        if unresolved and unresolved != [len(directories)]:
            raise KernelCampaignJournalError("a later provider call follows an unresolved call claim")
        return tuple(failures), tuple(unresolved)

    def generation_activity(self) -> tuple[list[KernelGenerationResult], tuple[KernelGenerationFailure, ...]]:
        results = self.generation_results()
        completed = {result.proposal_index for result in results}
        durable_failures: dict[str, KernelGenerationFailure] = {}
        for proposal_dir in sorted(self.root.glob("proposals/*")):
            if not proposal_dir.is_dir() or not proposal_dir.name.isdigit():
                raise KernelCampaignJournalError("generation proposal path is malformed")
            proposal_index = int(proposal_dir.name)
            failures, _ = self.generation_call_state(proposal_index)
            if proposal_index not in completed:
                for failure in failures:
                    add_durable_failure(durable_failures, failure, proposal_index)
        for path in sorted(self.root.glob("proposals/*/failure.json")):
            payload = read_json(path)
            if (
                not isinstance(payload, dict)
                or payload.get("schema_version")
                != "autocontext.kernel-generation-terminal-failure/v1"
                or payload.get("run_id") != self.run_id
            ):
                raise KernelCampaignJournalError("generation failure journal identity is invalid")
            failure_proposal = payload.get("proposal_index")
            raw_failures = payload.get("failures")
            if (
                not isinstance(failure_proposal, int)
                or path.parent.name != f"{failure_proposal:06d}"
                or not isinstance(raw_failures, list)
            ):
                raise KernelCampaignJournalError("generation failure journal is malformed")
            parsed = [KernelGenerationFailure.model_validate(item) for item in raw_failures]
            if failure_proposal not in completed:
                for failure in parsed:
                    add_durable_failure(durable_failures, failure, failure_proposal)
        for path in sorted(self.root.glob("proposals/*/cancellations/*.json")):
            payload = read_json(path)
            cancel_proposal = payload.get("proposal_index") if isinstance(payload, dict) else None
            if (
                not isinstance(payload, dict)
                or payload.get("schema_version") != "autocontext.kernel-generation-cancellation/v1"
                or payload.get("run_id") != self.run_id
                or not isinstance(cancel_proposal, int)
                or path.parent.parent.name != f"{cancel_proposal:06d}"
                or not isinstance(payload.get("failures"), list)
            ):
                raise KernelCampaignJournalError("generation cancellation journal is malformed")
            if cancel_proposal not in completed:
                for item in payload["failures"]:
                    add_durable_failure(
                        durable_failures,
                        KernelGenerationFailure.model_validate(item),
                        cancel_proposal,
                    )
        return results, tuple(durable_failures.values())

    def budget_state(self) -> KernelGenerationBudgetState:
        results, failures = self.generation_activity()
        return KernelGenerationBudgetState.from_activity(results, failures)

    def assert_resumable(
        self,
        *,
        attempts_by_id: set[str],
        expected_generation_identity: str,
        claim_resume_safe: bool = False,
        call_fence_resume_safe: bool = False,
    ) -> None:
        claim_paths = sorted(self.root.glob("proposals/*/claim.json"))
        claim_names = [path.parent.name for path in claim_paths]
        if claim_names != [f"{index:06d}" for index in range(1, len(claim_paths) + 1)]:
            raise KernelCampaignJournalError("generation claims are not contiguous from proposal one")
        if len(claim_paths) > len(self.generation_results()) + 1:
            raise KernelCampaignJournalError("multiple incomplete generation proposals are journaled")
        for claim_path in claim_paths:
            if not claim_path.parent.name.isdigit():
                raise KernelCampaignJournalError("generation claim path is malformed")
            proposal_index = int(claim_path.parent.name)
            if claim_path.parent.name != f"{proposal_index:06d}":
                raise KernelCampaignJournalError("generation claim path is not canonical")
            generation_claim = self.read_generation_claim(proposal_index)
            if generation_claim is None:
                raise KernelCampaignJournalError("generation claim disappeared during validation")
            if generation_claim.generator_identity != expected_generation_identity:
                raise KernelCampaignJournalError("generation claim generator identity changed")
            if self.read_generation_result(generation_claim.proposal_index) is None:
                _, unresolved = self.generation_call_state(generation_claim.proposal_index)
                resume_safe = not unresolved and (call_fence_resume_safe or claim_resume_safe)
                if resume_safe:
                    continue
                raise KernelCampaignAmbiguousExecution(
                    f"proposal {generation_claim.proposal_index} has a pre-dispatch claim without a result; "
                    "provider execution is ambiguous and will not be repeated"
                )
        claimed_attempts: set[str] = set()
        for claim_path in sorted(self.root.glob("evaluations/*.json")):
            evaluation_claim = self._read_evaluation_claim(claim_path)
            claimed_attempts.add(evaluation_claim.attempt_id)
            if evaluation_claim.attempt_id not in attempts_by_id:
                raise KernelCampaignAmbiguousExecution(
                    f"generation {evaluation_claim.generation} has an evaluation claim without an attempt record; "
                    "benchmark execution is ambiguous and will not be repeated"
                )
        if attempts_by_id != claimed_attempts:
            raise KernelCampaignJournalError(
                "persisted kernel attempts do not match their durable evaluation claims"
            )
        for path in sorted(self.root.glob("proposals/*/attempt-link.json")):
            proposal_name = path.parent.name
            if not proposal_name.isdigit() or proposal_name != f"{int(proposal_name):06d}":
                raise KernelCampaignJournalError("generation attempt-link path is malformed")
            proposal_index = int(proposal_name)
            link = KernelGenerationAttemptLink.model_validate(read_json(path))
            result = self.read_generation_result(proposal_index)
            expected_attempt = deterministic_kernel_attempt_id(
                self.run_id,
                generation=proposal_index,
                artifact_digest=link.artifact_digest,
                generation_receipt_id=link.generation_receipt_id,
            )
            if (
                link.run_id != self.run_id
                or link.proposal_index != proposal_index
                or result is None
                or link.generation_receipt_id != result.receipt_id
                or link.attempt_id != expected_attempt
                or link.attempt_id not in attempts_by_id
            ):
                raise KernelCampaignJournalError("generation attempt-link identity is invalid")

    def evaluation_claim_attempt_ids(self) -> set[str]:
        return {self._read_evaluation_claim(path).attempt_id for path in sorted(self.root.glob("evaluations/*.json"))}

    def request_stop(self, *, requested_by: str = "operator") -> Path:
        if not requested_by.strip():
            raise ValueError("requested_by must not be empty")
        path = self.run_dir / "control" / "stop.json"
        payload = {
            "schema_version": "autocontext.kernel-stop-request/v1",
            "run_id": self.run_id,
            "requested_by": requested_by.strip(),
            "requested_at": datetime.now(UTC).isoformat(),
        }
        with advisory_path_lock(self._dispatch_lock_path()):
            if not path.exists():
                write_json(path, payload)
        return path

    def stop_requested(self) -> bool:
        return (self.run_dir / "control" / "stop.json").is_file()

    def clear_stop_for_resume(self) -> None:
        path = self.run_dir / "control" / "stop.json"
        with advisory_path_lock(self._dispatch_lock_path()):
            if path.exists():
                consumed = self.run_dir / "control" / "consumed"
                consumed.mkdir(parents=True, exist_ok=True)
                path.replace(consumed / f"stop-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}.json")

    def refresh_artifact_index(self) -> KernelRunArtifactIndex:
        entries: list[KernelRunArtifact] = []
        index_path = self.run_dir / "artifact-index.json"
        if self.run_dir.exists():
            for path in sorted(item for item in self.run_dir.rglob("*") if item.is_file()):
                if path == index_path or path.suffix == ".lock" or ".tmp" in path.name:
                    continue
                relative = path.relative_to(self.run_dir).as_posix()
                content = path.read_bytes()
                entries.append(
                    KernelRunArtifact(
                        kind=artifact_kind(relative),
                        path=relative,
                        digest=content_digest(content),
                        size_bytes=len(content),
                    )
                )
        index = KernelRunArtifactIndex(
            run_id=self.run_id,
            artifacts=tuple(entries),
            generated_at=datetime.now(UTC).isoformat(),
        )
        write_text_atomic(index_path, index.model_dump_json(indent=2))
        return index

    def status(self, *, generation_budget: KernelGenerationBudget | None = None) -> KernelCampaignStatus:
        manifest_path = self.run_dir / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"kernel campaign manifest not found: {manifest_path}")
        manifest = read_json(manifest_path)
        if not isinstance(manifest, dict) or manifest.get("run_id") != self.run_id:
            raise KernelCampaignJournalError("kernel campaign manifest identity is invalid")
        results = self.generation_results()
        attempts = tuple((self.run_dir / "attempts").glob("*.json")) if (self.run_dir / "attempts").exists() else ()
        links = tuple(self.root.glob("proposals/*/attempt-link.json"))
        generation_contract = manifest.get("generation")
        if not isinstance(generation_contract, dict):
            raise KernelCampaignJournalError("kernel campaign manifest has no generation contract")
        generator_identity = generation_contract.get("generator_identity")
        if not isinstance(generator_identity, str):
            raise KernelCampaignJournalError("kernel campaign manifest has no generator identity")
        ambiguity: str | None = None
        try:
            self.assert_resumable(
                attempts_by_id={path.stem for path in attempts},
                expected_generation_identity=generator_identity,
                claim_resume_safe=generation_contract.get("claim_resume_safe") is True,
                call_fence_resume_safe=generation_contract.get("call_fence_resume_safe") is True,
            )
        except KernelCampaignAmbiguousExecution as exc:
            ambiguity = str(exc)
        self.refresh_artifact_index()
        budget_state = self.budget_state()
        status = str(manifest.get("status", "unknown"))
        terminal = status in {"complete", "baseline_failed", "failed"}
        manifest_budget_id = (
            generation_contract.get("budget_id")
            if isinstance(generation_contract, dict)
            and isinstance(generation_contract.get("budget_id"), str)
            else None
        )
        if (
            generation_budget is not None
            and manifest_budget_id is not None
            and generation_budget.budget_id != manifest_budget_id
        ):
            raise KernelCampaignJournalError("status budget conflicts with the persisted campaign budget")
        return KernelCampaignStatus(
            run_id=self.run_id,
            status=status,
            problem_id=manifest.get("problem_id") if isinstance(manifest.get("problem_id"), str) else None,
            proposals_requested=(
                int(manifest["proposals_requested"])
                if isinstance(manifest.get("proposals_requested"), int)
                else None
            ),
            proposals_generated=len(results),
            proposals_evaluated=len(links),
            attempts_persisted=len(attempts),
            champion_attempt_id=(
                manifest.get("champion_attempt_id")
                if isinstance(manifest.get("champion_attempt_id"), str)
                else None
            ),
            champion_artifact_digest=champion_artifact_digest(self.run_dir),
            generation_budget_id=(
                generation_budget.budget_id
                if generation_budget is not None
                else manifest_budget_id
            ),
            generation_budget_state=budget_state,
            stop_requested=self.stop_requested(),
            can_resume=not terminal and ambiguity is None,
            ambiguity=ambiguity,
            artifact_index_path=str((self.run_dir / "artifact-index.json").resolve()),
        )

    def _generation_dir(self, proposal_index: int) -> Path:
        if proposal_index < 1:
            raise ValueError("proposal_index must be positive")
        return self.root / "proposals" / f"{proposal_index:06d}"

    def _generation_call_dir(self, proposal_index: int, call_index: int) -> Path:
        if call_index < 1:
            raise ValueError("call_index must be positive")
        return self._generation_dir(proposal_index) / "calls" / f"{call_index:06d}"

    def _dispatch_lock_path(self) -> Path:
        return self.run_dir / "control" / "dispatch.lock"

    def _evaluation_claim_path(self, generation: int) -> Path:
        if generation < 0:
            raise ValueError("generation must be non-negative")
        return self.root / "evaluations" / f"{generation:06d}.json"

    def _read_evaluation_claim(self, path: Path) -> KernelEvaluationClaim:
        if not path.stem.isdigit():
            raise KernelCampaignJournalError("evaluation claim path is malformed")
        generation = int(path.stem)
        if path.name != f"{generation:06d}.json":
            raise KernelCampaignJournalError("evaluation claim path is not canonical")
        claim = KernelEvaluationClaim.model_validate(read_json(path))
        expected_attempt_id = deterministic_kernel_attempt_id(
            self.run_id,
            generation=generation,
            artifact_digest=claim.artifact_digest,
            generation_receipt_id=claim.generation_receipt_id,
        )
        if (
            claim.run_id != self.run_id
            or claim.generation != generation
            or claim.attempt_id != expected_attempt_id
        ):
            raise KernelCampaignJournalError("evaluation claim identity is invalid")
        return claim

    def _validate_call_claim(
        self,
        claim: KernelGenerationCallClaim,
        proposal_index: int,
        call_index: int,
    ) -> None:
        if (
            claim.run_id != self.run_id
            or claim.proposal_index != proposal_index
            or claim.call_index != call_index
        ):
            raise KernelCampaignJournalError("generation call claim identity is invalid")

    def _validate_prompt_artifact(self, digest: str, *, label: str) -> None:
        path = self.run_dir / "prompts" / f"{digest.removeprefix('sha256:')}.md"
        if not path.is_file() or content_digest(path.read_bytes()) != digest:
            raise KernelCampaignJournalError(f"{label} artifact is missing or changed")

    def _generation_pointer(self, result: KernelGenerationResult, receipt_path: Path) -> dict[str, Any]:
        return {
            "schema_version": "autocontext.kernel-generation-pointer/v1",
            "run_id": self.run_id,
            "proposal_index": result.proposal_index,
            "generation_receipt_id": result.receipt_id,
            "receipt_path": receipt_path.relative_to(self.run_dir).as_posix(),
        }

    def _orphan_generation_result(
        self,
        proposal_index: int,
    ) -> tuple[Path, KernelGenerationResult] | None:
        matches: list[tuple[Path, KernelGenerationResult]] = []
        for path in sorted((self.root / "receipts").glob("*.json")):
            result = KernelGenerationResult.model_validate(read_json(path))
            if path.name != f"{result.receipt_id.removeprefix('sha256:')}.json":
                raise KernelCampaignJournalError("generation receipt path does not match its content")
            if result.proposal_index == proposal_index:
                self._validate_generation_result(result, proposal_index, result.receipt_id)
                matches.append((path, result))
        if len(matches) > 1:
            raise KernelCampaignJournalError("multiple generation receipts claim the same proposal")
        return matches[0] if matches else None

    @staticmethod
    def _write_exact_text(path: Path, content: str) -> None:
        if path.exists():
            if path.read_bytes() != content.encode("utf-8"):
                raise KernelCampaignJournalError(f"content-addressed prompt changed at {path}")
            return
        write_text_atomic(path, content)

    @staticmethod
    def _write_immutable_json(path: Path, payload: dict[str, Any]) -> None:
        serialized = json.dumps(payload, indent=2, sort_keys=True)
        if path.exists():
            if path.read_text(encoding="utf-8") != serialized:
                raise KernelCampaignJournalError(f"immutable campaign journal entry changed at {path}")
            return
        write_text_atomic(path, serialized)


def deterministic_kernel_attempt_id(
    run_id: str,
    *,
    generation: int,
    artifact_digest: str,
    generation_receipt_id: str | None,
) -> str:
    digest = canonical_digest(
        {
            "kind": "kernel-attempt-id/v1",
            "run_id": run_id,
            "generation": generation,
            "artifact_digest": artifact_digest,
            "generation_receipt_id": generation_receipt_id,
        }
    ).removeprefix("sha256:")
    return f"attempt_{digest[:32]}"


def read_kernel_campaign_status(
    lineage_root: Path,
    run_id: str,
    *,
    generation_budget: KernelGenerationBudget | None = None,
) -> KernelCampaignStatus:
    return KernelCampaignJournal(lineage_root / run_id, run_id).status(generation_budget=generation_budget)


def request_kernel_campaign_stop(lineage_root: Path, run_id: str, *, requested_by: str = "operator") -> Path:
    run_dir = lineage_root / run_id
    if not run_dir.is_dir():
        raise FileNotFoundError(f"kernel campaign not found: {run_id}")
    return KernelCampaignJournal(run_dir, run_id).request_stop(requested_by=requested_by)


__all__ = [
    "KernelCampaignAmbiguousExecution",
    "KernelCampaignJournal",
    "KernelCampaignJournalError",
    "KernelCampaignStatus",
    "KernelEvaluationClaim",
    "KernelGenerationAttemptLink",
    "KernelGenerationClaim",
    "KernelRunArtifact",
    "KernelRunArtifactIndex",
    "deterministic_kernel_attempt_id",
    "read_kernel_campaign_status",
    "request_kernel_campaign_stop",
]
