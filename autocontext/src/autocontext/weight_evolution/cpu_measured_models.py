"""Versioned outcomes for the measured parent around the frozen CPU worker."""

from __future__ import annotations

import json
from typing import Any, Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_failure_v2_models import CpuFailureContractV2, CpuFailureRecordV2
from autocontext.weight_evolution.cpu_measured_transport import CpuProcessObservation
from autocontext.weight_evolution.cpu_runner import CpuBatchReceipt
from autocontext.weight_evolution.models import Digest, FrozenModel

MAX_MEASURED_OUTCOME_BYTES = 512 * 1024


class CpuMeasuredBatchOutcome(FrozenModel):
    schema_version: Literal[1] = 1
    kind: Literal["measured-cpu-batch-outcome-v2"] = "measured-cpu-batch-outcome-v2"
    contract: CpuFailureContractV2
    profile_digest: Digest
    native_runtime_digest: Digest
    batch_digest: Digest
    request_digests: tuple[Digest, ...] = Field(min_length=1, max_length=32)
    checkpoint_sha256: Digest
    checkpoint_size_bytes: int = Field(ge=10, le=4 * 1024 * 1024)
    model_digest: Digest
    status: Literal["completed", "failed"]
    failure_phase: Literal["preflight", "transport", "response-validation", "postcheck", "cleanup"] | None
    process: CpuProcessObservation
    receipt: CpuBatchReceipt | None = None
    failure: CpuFailureRecordV2 | None = None
    cleanup_failure: CpuFailureRecordV2 | None = None
    gpu_execution_deferred: Literal[True] = True
    promotion_eligible: Literal[False] = False
    retry_permitted: Literal[False] = False
    refund_permitted: Literal[False] = False

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @field_validator("schema_version", "gpu_execution_deferred", "promotion_eligible", "retry_permitted",
                     "refund_permitted", mode="before")
    @classmethod
    def literal_types(cls, value: Any, info: ValidationInfo) -> Any:
        if type(value) is not (int if info.field_name == "schema_version" else bool):
            raise ValueError("incorrect measured outcome literal type")
        return value

    @model_validator(mode="after")
    def consistent_outcome(self) -> Self:
        if self.batch_digest != canonical_digest({"request_digests": list(self.request_digests),
                                                   "runtime_digest": self.native_runtime_digest}):
            raise ValueError("measured batch digest differs from its requests/runtime")
        if self.status == "completed":
            if (self.receipt is None or self.failure is not None or self.cleanup_failure is not None
                    or self.failure_phase is not None or not self.process.dispatched
                    or self.process.trigger != "completed" or self.process.cleanup_state != "completed"
                    or self.process.returncode_before_cleanup != 0 or self.process.returncode_after_cleanup != 0
                    or self.process.leader_reaped is not True):
                raise ValueError("completed outcome requires verified success and completed cleanup")
        elif (self.receipt is not None or self.failure_phase is None
              or (self.failure is None and self.cleanup_failure is None)):
            raise ValueError("failed outcome requires retained failure evidence and cannot accept a receipt")
        if (self.failure_phase == "cleanup") != (self.cleanup_failure is not None and self.failure is None):
            raise ValueError("cleanup-only failure phase differs from retained primary/cleanup evidence")
        if self.cleanup_failure is not None and self.process.cleanup_state not in {"failed", "ownership-lost"}:
            raise ValueError("cleanup failure requires unsuccessful cleanup observations")
        if self.process.cleanup_state == "failed" and self.cleanup_failure is None:
            raise ValueError("failed cleanup requires its separate exception evidence")
        if self.failure_phase == "preflight" and (self.process.dispatched or self.process.trigger != "preflight-failed"):
            raise ValueError("preflight failure requires a nondispatched preflight observation")
        if self.failure_phase in {"response-validation", "postcheck"}:
            if (not self.process.dispatched or self.process.trigger != "completed"
                    or self.process.cleanup_state != "completed" or self.process.leader_reaped is not True
                    or self.process.returncode_before_cleanup != 0 or self.process.returncode_after_cleanup != 0):
                raise ValueError("response/postcheck failure requires a completed measured transport")
        if self.failure_phase == "transport" and self.process.trigger in {"completed", "cleanup-failed"}:
            raise ValueError("transport failure phase differs from its trigger")
        for record in (self.failure, self.cleanup_failure):
            if record is not None and (record.contract != self.contract or record.supervisor_digest != self.profile_digest
                    or record.study_digest != self.batch_digest or record.runtime_digest != self.native_runtime_digest
                    or record.run_id != "measured-batch" or record.native_journal_kind != "absent"
                    or record.observation_kind != "live-exception"):
                raise ValueError("failure evidence differs from the measured batch/profile binding")
        if self.receipt is not None:
            for field in ("batch_digest", "request_digests", "checkpoint_sha256", "checkpoint_size_bytes", "model_digest"):
                if getattr(self.receipt, field) != getattr(self, field):
                    raise ValueError("measured receipt differs from its outcome binding")
            if self.receipt.runtime_digest != self.native_runtime_digest:
                raise ValueError("measured receipt differs from native worker runtime")
        encoded = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) > MAX_MEASURED_OUTCOME_BYTES:
            raise ValueError("measured outcome exceeds its serialized byte cap")
        return self
