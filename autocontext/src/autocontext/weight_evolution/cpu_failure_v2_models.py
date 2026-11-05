"""Versioned bounded failure evidence with collision-safe text omission."""

from __future__ import annotations

import json
from typing import Any, Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_models import CpuWorkerLimits
from autocontext.weight_evolution.models import Digest, FrozenModel, Identifier

MAX_FAILURE_RECORD_BYTES = 128 * 1024


class _FailureModel(FrozenModel):
    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @field_validator("schema_version", "gpu_execution_deferred", "retry_permitted", "refund_permitted",
                     "causal_retroclaim_permitted", mode="before", check_fields=False)
    @classmethod
    def literal_types(cls, value: Any, info: ValidationInfo) -> Any:
        expected = int if info.field_name == "schema_version" else bool
        if type(value) is not expected:
            raise ValueError(f"{info.field_name} has the wrong literal type")
        return value


class CpuFailureContractV2(_FailureModel):
    schema_version: Literal[2] = 2
    kind: Literal["bounded-cpu-failure-observation-v2"] = "bounded-cpu-failure-observation-v2"
    redaction_rule: Literal["normalized-span-replacement-or-empty-v1"] = "normalized-span-replacement-or-empty-v1"
    redaction_marker: Literal["[redacted]"] = "[redacted]"
    max_exception_nodes: int = Field(default=8, ge=1, le=16)
    max_message_bytes: int = Field(default=1024, ge=32, le=4096)
    max_stream_bytes: int = Field(default=2048, ge=32, le=4096)
    max_type_bytes: int = Field(default=256, ge=32, le=512)
    max_record_bytes: int = Field(default=65536, ge=4096, le=MAX_FAILURE_RECORD_BYTES)
    retry_permitted: Literal[False] = False
    refund_permitted: Literal[False] = False
    gpu_execution_deferred: Literal[True] = True
    causal_retroclaim_permitted: Literal[False] = False
    attribution_rule: Literal["observed-exception-not-verified-root-cause"] = "observed-exception-not-verified-root-cause"


class CpuFailureTextV2(_FailureModel):
    text: str = Field(max_length=4096)
    truncated: bool = False
    redacted: bool = False
    encoding_replaced: bool = False
    rendering_failed: bool = False
    omitted: bool = False
    omission_reason: Literal["redaction-collision", "rendering-failed"] | None = None

    @model_validator(mode="after")
    def explicit_omission(self) -> Self:
        if self.omitted != (self.omission_reason is not None) or (self.omitted and self.text):
            raise ValueError("omitted text must be empty and have its explicit reason")
        if self.omission_reason == "redaction-collision" and not self.redacted:
            raise ValueError("redaction omission must be identified as redacted")
        if self.omission_reason == "rendering-failed" and not self.rendering_failed:
            raise ValueError("rendering omission must identify the rendering failure")
        return self

    @field_validator("text")
    @classmethod
    def utf8_bound(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 4096:
            raise ValueError("failure text exceeds its UTF-8 byte bound")
        return value


class CpuFailureObservationsV2(_FailureModel):
    """Only supplied measurements; absent telemetry remains explicitly unknown."""

    returncode: int | None = Field(default=None, ge=-255, le=255)
    signal: int | None = Field(default=None, ge=1, le=255)
    elapsed_seconds: float | None = Field(default=None, ge=0.0, le=3600.0, allow_inf_nan=False)
    peak_rss_bytes: int | None = Field(default=None, ge=0, le=2**40)
    stdout: CpuFailureTextV2 | None = None
    stderr: CpuFailureTextV2 | None = None

    @model_validator(mode="after")
    def consistent_explicit_signal(self) -> Self:
        if self.signal is not None and self.returncode is not None and self.returncode != -self.signal:
            raise ValueError("explicit signal and subprocess return code disagree")
        return self


class CpuFailureExceptionNodeV2(_FailureModel):
    node_id: int = Field(ge=0, lt=16)
    exception_type: str = Field(min_length=1, max_length=512)
    exception_module: str = Field(min_length=1, max_length=512)
    type_truncated: bool = False
    module_truncated: bool = False
    message: CpuFailureTextV2
    cause_id: int | None = Field(default=None, ge=0, lt=16)
    context_id: int | None = Field(default=None, ge=0, lt=16)
    cause_truncated: bool = False
    context_truncated: bool = False
    suppress_context: bool = False

    @model_validator(mode="after")
    def unambiguous_edges(self) -> Self:
        if (self.cause_id is not None and self.cause_truncated) or (self.context_id is not None and self.context_truncated):
            raise ValueError("exception edge cannot be both retained and truncated")
        return self


class CpuFailureRequestIdentityV2(_FailureModel):
    request_id: Identifier = Field(max_length=128)
    request_digest: Digest
    model_digest: Digest
    decoding_digest: Digest
    checkpoint_sha256: Digest
    checkpoint_size_bytes: int = Field(ge=10, le=4 * 1024 * 1024)
    declared_limits: CpuWorkerLimits


class CpuFailureCheckpointIdentityV2(_FailureModel):
    sha256: Digest
    size_bytes: int = Field(ge=10, le=4 * 1024 * 1024)


def exception_graph_has_cycle(nodes: tuple[CpuFailureExceptionNodeV2, ...]) -> bool:
    complete: set[int] = set()
    active: set[int] = set()

    def visit(index: int) -> bool:
        if index in active:
            return True
        if index in complete:
            return False
        active.add(index)
        node = nodes[index]
        if any(visit(edge) for edge in (node.cause_id, node.context_id) if edge is not None):
            return True
        active.remove(index)
        complete.add(index)
        return False

    return bool(nodes) and visit(0)


class CpuFailureRecordV2(_FailureModel):
    schema_version: Literal[2] = 2
    kind: Literal["cpu-failure-record-v2"] = "cpu-failure-record-v2"
    contract: CpuFailureContractV2
    contract_digest: Digest
    redaction_digest: Digest
    redaction_count: int = Field(ge=0, le=64)
    supervisor_digest: Digest
    run_id: Identifier = Field(max_length=128)
    study_digest: Digest
    runtime_digest: Digest
    native_journal_kind: Literal["search", "reference", "absent"]
    diagnostic_digest: Digest | None = None
    terminal_claim_digest: Digest | None
    terminal_result_digest: Digest | None
    terminal_status: Literal["failed", "completed", "uncertain", "absent"]
    operation_index: int | None = Field(default=None, ge=0, le=4095)
    operation_kind: Literal["materialize", "proposal", "inference", "checkpoint-verification"] | None = None
    operation_label: Identifier | None = Field(default=None, max_length=128)
    operation_input_digest: Digest | None = None
    candidate_digest: Digest | None = None
    requests: tuple[CpuFailureRequestIdentityV2, ...] = Field(default=(), max_length=32)
    checkpoints: tuple[CpuFailureCheckpointIdentityV2, ...] = Field(default=(), max_length=1)
    observation_kind: Literal["live-exception", "retained-class-only", "observation-lost"]
    retained_error_type: str | None = Field(default=None, min_length=1, max_length=512)
    retained_type_truncated: bool = False
    exceptions: tuple[CpuFailureExceptionNodeV2, ...] = Field(default=(), max_length=16)
    exception_chain_truncated: bool = False
    exception_cycle_detected: bool = False
    observations: CpuFailureObservationsV2 = Field(default_factory=CpuFailureObservationsV2)
    attribution: Literal["terminal-binding-does-not-attribute-failure-to-operation"] = (
        "terminal-binding-does-not-attribute-failure-to-operation"
    )

    @model_validator(mode="after")
    def bounded_evidence(self) -> Self:
        if self.contract_digest != self.contract.digest:
            raise ValueError("failure record contract pin differs")
        if self.terminal_claim_digest is None:
            if (self.terminal_result_digest is not None or self.terminal_status != "absent"
                    or self.native_journal_kind != "absent" or self.diagnostic_digest is not None
                    or any(value is not None for value in (self.operation_index, self.operation_kind,
                        self.operation_label, self.operation_input_digest, self.candidate_digest))
                    or self.requests or self.checkpoints):
                raise ValueError("failure without a terminal claim cannot invent operation identities")
        elif (any(value is None for value in (self.operation_index, self.operation_kind,
                                              self.operation_label, self.operation_input_digest))
              or self.native_journal_kind == "absent"
              or self.terminal_status == "absent"
              or (self.terminal_status == "uncertain") != (self.terminal_result_digest is None)):
            raise ValueError("failure terminal state differs from claim/result availability")
        if (self.native_journal_kind == "reference") != (self.diagnostic_digest is not None):
            raise ValueError("reference failure observations require their native diagnostic identity")
        if (self.retained_error_type is not None) != (self.terminal_status == "failed"):
            raise ValueError("retained error class requires an exact failed terminal result")
        if self.retained_error_type is None and self.retained_type_truncated:
            raise ValueError("absent retained exception type cannot be truncated")
        if self.operation_kind != "inference" and self.requests:
            raise ValueError("only inference operations have request identities")
        if self.operation_kind in {"inference", "materialize"}:
            if self.candidate_digest is None or len(self.checkpoints) != 1:
                raise ValueError("checkpoint operation requires its candidate and checkpoint identities")
            if self.operation_kind == "inference" and not self.requests:
                raise ValueError("inference operation requires its bounded request identities")
        elif self.operation_kind == "checkpoint-verification":
            if self.candidate_digest is None or self.checkpoints or self.native_journal_kind != "reference":
                raise ValueError("checkpoint verification binds only its retained native candidate digest")
        elif self.candidate_digest is not None or self.checkpoints:
            raise ValueError("noncheckpoint operation cannot invent checkpoint identities")
        if self.observation_kind == "live-exception":
            if not self.exceptions:
                raise ValueError("live observation requires an exception graph")
        elif self.exceptions or self.exception_chain_truncated or self.exception_cycle_detected:
            raise ValueError("retained or lost observations cannot invent a live exception graph")
        if self.observation_kind == "retained-class-only" and self.retained_error_type is None:
            raise ValueError("retained-class-only observation requires a retained exception class")
        if self.observation_kind == "observation-lost" and self.retained_error_type is not None:
            raise ValueError("known retained exception class must be identified as class-only evidence")
        if len(self.exceptions) > self.contract.max_exception_nodes:
            raise ValueError("exception graph exceeds its pinned node cap")
        if [node.node_id for node in self.exceptions] != list(range(len(self.exceptions))):
            raise ValueError("exception nodes must form a contiguous identity inventory")
        reachable = {0} if self.exceptions else set()
        for _ in self.exceptions:
            for index in tuple(reachable):
                for edge in (self.exceptions[index].cause_id, self.exceptions[index].context_id):
                    if edge is not None:
                        if edge >= len(self.exceptions):
                            raise ValueError("exception graph has an unknown edge")
                        reachable.add(edge)
        if len(reachable) != len(self.exceptions):
            raise ValueError("exception graph includes unreachable observations")
        if self.exception_cycle_detected != exception_graph_has_cycle(self.exceptions):
            raise ValueError("exception cycle flag differs from the retained graph")
        if self.exception_chain_truncated != any(node.cause_truncated or node.context_truncated for node in self.exceptions):
            raise ValueError("exception truncation flag differs from the retained graph")
        for node in self.exceptions:
            if (len(node.message.text.encode()) > self.contract.max_message_bytes
                    or any(len(value.encode()) > self.contract.max_type_bytes
                           for value in (node.exception_type, node.exception_module))):
                raise ValueError("exception text exceeds its pinned byte cap")
        if self.retained_error_type is not None and len(self.retained_error_type.encode()) > self.contract.max_type_bytes:
            raise ValueError("retained exception type exceeds its pinned byte cap")
        for value in (self.observations.stdout, self.observations.stderr):
            if value is not None and len(value.text.encode()) > self.contract.max_stream_bytes:
                raise ValueError("observed stream exceeds its pinned byte cap")
        encoded = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) > self.contract.max_record_bytes:
            raise ValueError("failure record exceeds its pinned serialized byte cap")
        return self
