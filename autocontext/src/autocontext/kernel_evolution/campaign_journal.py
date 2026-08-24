"""Durable proposal journal, stop control, status, and artifact discovery."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from autocontext.kernel_evolution.generation import (
    KernelGenerationBudget,
    KernelGenerationBudgetState,
    KernelGenerationFailure,
    KernelGenerationResult,
)
from autocontext.kernel_evolution.models import KernelCandidate, StrictModel, canonical_digest, content_digest
from autocontext.util.json_io import read_json, write_json, write_text_atomic

_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
KernelArtifactKind = Literal[
    "manifest",
    "prompt",
    "generation_claim",
    "generation_receipt",
    "generation_failure",
    "evaluation_claim",
    "attempt_link",
    "source",
    "report",
    "attempt",
    "lineage",
    "champion",
    "summary",
    "profile_evidence",
    "audit",
    "other",
]


class KernelCampaignJournalError(RuntimeError):
    """Durable campaign journal is incomplete, conflicting, or ambiguous."""


class KernelCampaignAmbiguousExecution(KernelCampaignJournalError):
    """A dispatch may have occurred and must never be duplicated automatically."""


class KernelGenerationClaim(StrictModel):
    schema_version: Literal["autocontext.kernel-generation-claim/v1"] = (
        "autocontext.kernel-generation-claim/v1"
    )
    run_id: str
    proposal_index: int = Field(ge=1)
    prompt_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    system_prompt_digest: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    generator_identity: str = Field(min_length=1)
    created_at: str

    @property
    def claim_id(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))


class KernelEvaluationClaim(StrictModel):
    schema_version: Literal["autocontext.kernel-evaluation-claim/v1"] = (
        "autocontext.kernel-evaluation-claim/v1"
    )
    run_id: str
    generation: int = Field(ge=0)
    role: Literal["baseline", "candidate"]
    generation_receipt_id: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    attempt_id: str = Field(pattern=r"^attempt_[0-9a-f]{32}$")
    created_at: str

    @model_validator(mode="after")
    def validate_role(self) -> Self:
        if self.role == "baseline" and (self.generation != 0 or self.generation_receipt_id is not None):
            raise ValueError("baseline evaluation claims cannot reference generation receipts")
        if self.role == "candidate" and (self.generation < 1 or self.generation_receipt_id is None):
            raise ValueError("candidate evaluation claims require a generation receipt")
        return self


class KernelGenerationAttemptLink(StrictModel):
    schema_version: Literal["autocontext.kernel-generation-attempt-link/v1"] = (
        "autocontext.kernel-generation-attempt-link/v1"
    )
    run_id: str
    proposal_index: int = Field(ge=1)
    generation_receipt_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    attempt_id: str = Field(pattern=r"^attempt_[0-9a-f]{32}$")
    artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class KernelRunArtifact(StrictModel):
    kind: KernelArtifactKind
    path: str
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class KernelRunArtifactIndex(StrictModel):
    schema_version: Literal["autocontext.kernel-artifact-index/v1"] = (
        "autocontext.kernel-artifact-index/v1"
    )
    run_id: str
    artifacts: tuple[KernelRunArtifact, ...]
    generated_at: str


class KernelCampaignStatus(StrictModel):
    schema_version: Literal["autocontext.kernel-campaign-status/v1"] = (
        "autocontext.kernel-campaign-status/v1"
    )
    run_id: str
    status: str
    problem_id: str | None = None
    proposals_requested: int | None = Field(default=None, ge=0)
    proposals_generated: int = Field(default=0, ge=0)
    proposals_evaluated: int = Field(default=0, ge=0)
    attempts_persisted: int = Field(default=0, ge=0)
    champion_attempt_id: str | None = None
    champion_artifact_digest: str | None = None
    generation_budget_id: str | None = None
    generation_budget_state: KernelGenerationBudgetState = Field(default_factory=KernelGenerationBudgetState)
    stop_requested: bool = False
    can_resume: bool
    ambiguity: str | None = None
    artifact_index_path: str


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
        self._write_immutable_json(self._generation_dir(proposal_index) / "claim.json", claim.model_dump(mode="json"))
        self.refresh_artifact_index()
        return claim

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
        pointer = {
            "schema_version": "autocontext.kernel-generation-pointer/v1",
            "run_id": self.run_id,
            "proposal_index": result.proposal_index,
            "generation_receipt_id": result.receipt_id,
            "receipt_path": str(receipt_path.relative_to(self.run_dir)),
        }
        self._write_immutable_json(self._generation_dir(result.proposal_index) / "result.json", pointer)
        self.refresh_artifact_index()
        return receipt_path

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

    def begin_evaluation(
        self,
        *,
        generation: int,
        role: Literal["baseline", "candidate"],
        artifact_digest: str,
        generation_receipt_id: str | None = None,
    ) -> KernelEvaluationClaim:
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
        self._write_immutable_json(self._evaluation_claim_path(generation), claim.model_dump(mode="json"))
        self.refresh_artifact_index()
        return claim

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
        return KernelGenerationClaim.model_validate(read_json(path))

    def read_generation_result(self, proposal_index: int) -> KernelGenerationResult | None:
        pointer_path = self._generation_dir(proposal_index) / "result.json"
        if not pointer_path.exists():
            return None
        pointer = read_json(pointer_path)
        if not isinstance(pointer, dict) or pointer.get("proposal_index") != proposal_index:
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
        prompt_path = self.run_dir / "prompts" / f"{result.prompt_digest.removeprefix('sha256:')}.md"
        if not prompt_path.is_file() or content_digest(prompt_path.read_bytes()) != result.prompt_digest:
            raise KernelCampaignJournalError("generation prompt artifact is missing or changed")
        if claim.system_prompt_digest is not None:
            system_prompt_path = self.run_dir / "prompts" / (
                f"{claim.system_prompt_digest.removeprefix('sha256:')}.md"
            )
            if (
                not system_prompt_path.is_file()
                or content_digest(system_prompt_path.read_bytes()) != claim.system_prompt_digest
            ):
                raise KernelCampaignJournalError("generation system-prompt artifact is missing or changed")
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
        return result

    def generation_results(self) -> list[KernelGenerationResult]:
        results: list[KernelGenerationResult] = []
        proposal_index = 1
        while True:
            result = self.read_generation_result(proposal_index)
            if result is None:
                break
            results.append(result)
            proposal_index += 1
        extra = sorted(self.root.glob("proposals/*/result.json"))
        if len(extra) != len(results):
            raise KernelCampaignJournalError("generation results are not contiguous from proposal one")
        return results

    def budget_state(self) -> KernelGenerationBudgetState:
        results = self.generation_results()
        completed = {result.proposal_index for result in results}
        terminal_failures: list[KernelGenerationFailure] = []
        for path in sorted(self.root.glob("proposals/*/failure.json")):
            payload = read_json(path)
            if not isinstance(payload, dict) or payload.get("run_id") != self.run_id:
                raise KernelCampaignJournalError("generation failure journal identity is invalid")
            proposal_index = payload.get("proposal_index")
            failures = payload.get("failures")
            if not isinstance(proposal_index, int) or not isinstance(failures, list):
                raise KernelCampaignJournalError("generation failure journal is malformed")
            if proposal_index in completed:
                continue
            parsed = [KernelGenerationFailure.model_validate(item) for item in failures]
            if any(item.proposal_index != proposal_index for item in parsed):
                raise KernelCampaignJournalError("generation failure belongs to a different proposal")
            terminal_failures.extend(parsed)
        return KernelGenerationBudgetState.from_activity(results, terminal_failures)

    def assert_resumable(
        self,
        *,
        attempts_by_id: set[str],
        resumable_generation_identity: str | None = None,
    ) -> None:
        for claim_path in sorted(self.root.glob("proposals/*/claim.json")):
            generation_claim = KernelGenerationClaim.model_validate(read_json(claim_path))
            if self.read_generation_result(generation_claim.proposal_index) is None:
                if generation_claim.generator_identity == resumable_generation_identity:
                    continue
                raise KernelCampaignAmbiguousExecution(
                    f"proposal {generation_claim.proposal_index} has a pre-dispatch claim without a result; "
                    "provider execution is ambiguous and will not be repeated"
                )
        claimed_attempts: set[str] = set()
        for claim_path in sorted(self.root.glob("evaluations/*.json")):
            evaluation_claim = KernelEvaluationClaim.model_validate(read_json(claim_path))
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

    def evaluation_claim_attempt_ids(self) -> set[str]:
        return {
            KernelEvaluationClaim.model_validate(read_json(path)).attempt_id
            for path in sorted(self.root.glob("evaluations/*.json"))
        }

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
        if not path.exists():
            write_json(path, payload)
        return path

    def stop_requested(self) -> bool:
        return (self.run_dir / "control" / "stop.json").is_file()

    def clear_stop_for_resume(self) -> None:
        path = self.run_dir / "control" / "stop.json"
        if path.exists():
            consumed = self.run_dir / "control" / "consumed"
            consumed.mkdir(parents=True, exist_ok=True)
            path.replace(consumed / f"stop-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}.json")

    def refresh_artifact_index(self) -> KernelRunArtifactIndex:
        entries: list[KernelRunArtifact] = []
        index_path = self.run_dir / "artifact-index.json"
        if self.run_dir.exists():
            for path in sorted(item for item in self.run_dir.rglob("*") if item.is_file()):
                if path == index_path or ".tmp" in path.name:
                    continue
                relative = path.relative_to(self.run_dir).as_posix()
                content = path.read_bytes()
                entries.append(
                    KernelRunArtifact(
                        kind=_artifact_kind(relative),
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
        resumable_generation_identity = (
            generation_contract.get("generator_identity")
            if isinstance(generation_contract, dict)
            and generation_contract.get("claim_resume_safe") is True
            and isinstance(generation_contract.get("generator_identity"), str)
            else None
        )
        ambiguity: str | None = None
        try:
            self.assert_resumable(
                attempts_by_id={path.stem for path in attempts},
                resumable_generation_identity=resumable_generation_identity,
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
            champion_artifact_digest=_champion_artifact_digest(self.run_dir),
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

    def _evaluation_claim_path(self, generation: int) -> Path:
        if generation < 0:
            raise ValueError("generation must be non-negative")
        return self.root / "evaluations" / f"{generation:06d}.json"

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


def _artifact_kind(relative: str) -> KernelArtifactKind:
    if relative == "manifest.json":
        return "manifest"
    if relative.startswith("prompts/"):
        return "prompt"
    if relative.endswith("/claim.json") and relative.startswith("generation/proposals/"):
        return "generation_claim"
    if relative.startswith("generation/receipts/"):
        return "generation_receipt"
    if relative.endswith("/failure.json"):
        return "generation_failure"
    if relative.startswith("generation/evaluations/"):
        return "evaluation_claim"
    if relative.endswith("/attempt-link.json"):
        return "attempt_link"
    if relative.startswith("artifacts/"):
        return "source"
    if relative.startswith("reports/"):
        return "report"
    if relative.startswith("attempts/"):
        return "attempt"
    if relative == "lineage.jsonl":
        return "lineage"
    if relative.startswith("champion"):
        return "champion"
    if relative == "summary.json":
        return "summary"
    if relative == "profile_evidence.json":
        return "profile_evidence"
    if relative.startswith("audit/"):
        return "audit"
    return "other"


def _champion_artifact_digest(run_dir: Path) -> str | None:
    path = run_dir / "champion.json"
    if not path.is_file():
        return None
    payload = read_json(path)
    value = payload.get("artifact_digest") if isinstance(payload, dict) else None
    return value if isinstance(value, str) else None


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
