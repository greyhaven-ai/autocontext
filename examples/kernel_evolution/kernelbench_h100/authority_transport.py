"""Trusted evaluator side of the isolated candidate authority protocol."""

from __future__ import annotations

import os
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import torch

from autocontext.kernel_evolution import (
    AuthorityMeasurement,
    AuthorityRequest,
    AuthorityResponse,
    AuthorityWireError,
    authority_message_digest,
    canonical_authority_digest,
    receive_authority_frame,
    send_authority_frame,
)
from autocontext.kernel_evolution.authority_tensor import deserialize_tensor_list, serialize_tensor_list


class CandidateAuthorityError(RuntimeError):
    """Bounded candidate failure surfaced without candidate-authored prose."""

    def __init__(self, kind: str, diagnostic_code: str) -> None:
        super().__init__(f"isolated candidate authority failed: {kind}/{diagnostic_code}")
        self.kind = kind
        self.diagnostic_code = diagnostic_code


@dataclass(slots=True)
class AuthorityRecorder:
    """Evaluator-owned global ordering for both isolated authority channels."""

    measurements: list[AuthorityMeasurement] = field(default_factory=list)
    _next_sequence: int = 0

    def reserve_sequence(self) -> int:
        sequence = self._next_sequence
        self._next_sequence += 1
        return sequence

    def record(self, measurement: AuthorityMeasurement) -> None:
        if measurement.sequence != len(self.measurements):
            raise RuntimeError("authority measurements lost their evaluator-owned order")
        self.measurements.append(measurement)


class CudaGlobalMemoryProbe:
    """Trusted evaluator observation of partition-global memory use."""

    def __init__(self) -> None:
        free, total = torch.cuda.mem_get_info()
        if free < 0 or total < 1 or free > total:
            raise RuntimeError("CUDA returned invalid global memory telemetry")
        self.total_bytes = int(total)

    def used_bytes(self) -> int:
        free, total = torch.cuda.mem_get_info()
        if int(total) != self.total_bytes or free < 0 or free > total:
            raise RuntimeError("CUDA partition capacity changed during authority evaluation")
        return int(total - free)


class AuthorityEndpoint:
    """Evaluator-owned listener for one untrusted candidate process."""

    def __init__(
        self,
        socket_path: Path,
        *,
        role: Literal["candidate", "incumbent"],
        artifact_digest: str,
        recorder: AuthorityRecorder,
        memory_probe: CudaGlobalMemoryProbe,
        timeout_seconds: float,
    ) -> None:
        self.socket_path = socket_path
        self.role = role
        self.artifact_digest = artifact_digest
        self.recorder = recorder
        self.memory_probe = memory_probe
        self.timeout_seconds = timeout_seconds
        self.session_nonce = canonical_authority_digest(os.urandom(32))
        self._listener: socket.socket | None = None
        self._connection: socket.socket | None = None

    def listen(self) -> None:
        if self._listener is not None:
            raise RuntimeError("authority endpoint is already listening")
        if self.socket_path.exists() or self.socket_path.is_symlink():
            raise RuntimeError("authority socket path already exists")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self.socket_path))
            self.socket_path.chmod(0o600)
            listener.listen(1)
            listener.settimeout(self.timeout_seconds)
        except Exception:
            listener.close()
            raise
        self._listener = listener

    def accept(self) -> None:
        listener = self._listener
        if listener is None:
            raise RuntimeError("authority endpoint must listen before accepting")
        connection, _ = listener.accept()
        connection.settimeout(self.timeout_seconds)
        self._connection = connection

    def initialize(self, init_inputs: list[torch.Tensor]) -> None:
        outputs, _measurement = self._exchange("initialize", init_inputs)
        if outputs:
            raise CandidateAuthorityError("protocol_corruption", "malformed_output")

    def execute(self, inputs: list[torch.Tensor]) -> tuple[list[torch.Tensor], AuthorityMeasurement]:
        return self._exchange("execute", inputs)

    def close(self) -> None:
        if self._connection is not None:
            try:
                self._exchange("shutdown", [])
            except (CandidateAuthorityError, OSError, RuntimeError, TimeoutError, AuthorityWireError):
                pass
            self._connection.close()
            self._connection = None
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        try:
            self.socket_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _exchange(
        self,
        operation: Literal["initialize", "execute", "shutdown"],
        tensors: list[torch.Tensor],
    ) -> tuple[list[torch.Tensor], AuthorityMeasurement]:
        connection = self._connection
        if connection is None:
            raise RuntimeError("authority endpoint is not connected")
        payload, manifest_digest = serialize_tensor_list(tensors, prefix="input")
        if operation == "shutdown":
            manifest_digest = canonical_authority_digest(b"")
        sequence = self.recorder.reserve_sequence()
        request = AuthorityRequest(
            request_id=canonical_authority_digest(
                f"{self.session_nonce}:{sequence}:{self.role}:{operation}:{canonical_authority_digest(payload)}"
            ),
            session_nonce=self.session_nonce,
            sequence=sequence,
            role=self.role,
            operation=operation,
            artifact_digest=self.artifact_digest,
            input_manifest_digest=manifest_digest,
            payload_digest=canonical_authority_digest(payload),
            payload_bytes=len(payload),
        )
        request_digest = authority_message_digest(request)
        baseline = self.memory_probe.used_bytes()
        peak = [baseline]
        sampling_errors: list[RuntimeError] = []
        stop = threading.Event()
        sampler = threading.Thread(target=self._sample_memory, args=(peak, stop, sampling_errors), daemon=True)
        started = time.perf_counter_ns()
        sampler.start()
        try:
            send_authority_frame(connection, request, payload)
            response, response_payload = receive_authority_frame(connection, AuthorityResponse)
            self._validate_response(request, response)
            outputs, output_manifest_digest = deserialize_tensor_list(response_payload, prefix="output")
            if response.output_manifest_digest != output_manifest_digest:
                raise AuthorityWireError("candidate output manifest digest is forged")
            outcome = {
                "complete": "complete",
                "candidate_error": "candidate_error",
                "oom": "oom",
                "protocol_error": "protocol_corruption",
            }[response.outcome]
            response_digest = authority_message_digest(response)
            diagnostic_code = response.diagnostic_code
        except (AuthorityWireError, OSError, TimeoutError) as exc:
            outcome = "protocol_corruption" if isinstance(exc, AuthorityWireError) else "candidate_crashed"
            response_digest = canonical_authority_digest(
                {"request_digest": request_digest, "evaluator_observed_error": outcome}
            )
            outputs = []
            response_payload = b""
            diagnostic_code = "malformed_output" if outcome == "protocol_corruption" else "execution"
        finally:
            finished = time.perf_counter_ns()
            stop.set()
            sampler.join(timeout=1.0)
            peak.append(self.memory_probe.used_bytes())
            if sampling_errors:
                raise RuntimeError("trusted accelerator memory sampling failed during candidate execution")
        measurement = AuthorityMeasurement(
            sequence=sequence,
            role=self.role,
            request_digest=request_digest,
            response_digest=response_digest,
            input_commitment=request.payload_digest,
            output_commitment=canonical_authority_digest(response_payload),
            elapsed_ns=max(1, finished - started),
            observed_peak_memory_bytes=max(0, max(peak) - baseline),
            outcome=outcome,
        )
        self.recorder.record(measurement)
        if outcome != "complete":
            raise CandidateAuthorityError(outcome, diagnostic_code)
        return outputs, measurement

    def _sample_memory(
        self,
        peak: list[int],
        stop: threading.Event,
        errors: list[RuntimeError],
    ) -> None:
        while not stop.wait(0.001):
            try:
                peak.append(self.memory_probe.used_bytes())
            except RuntimeError as exc:
                errors.append(exc)
                return

    def _validate_response(self, request: AuthorityRequest, response: AuthorityResponse) -> None:
        if (
            response.request_id != request.request_id
            or response.session_nonce != request.session_nonce
            or response.sequence != request.sequence
            or response.role != request.role
            or response.artifact_digest != request.artifact_digest
        ):
            raise AuthorityWireError("candidate response escaped its request, role, or artifact identity")


class RemoteAuthorityModel:
    """Torch-call-compatible proxy whose measurements are evaluator-owned."""

    def __init__(self, endpoint: AuthorityEndpoint) -> None:
        self.endpoint = endpoint
        self.last_measurement: AuthorityMeasurement | None = None

    def initialize(self, init_inputs: list[Any]) -> None:
        tensors = _require_tensor_list(init_inputs)
        self.endpoint.initialize(tensors)

    def __call__(self, *inputs: Any) -> torch.Tensor | tuple[torch.Tensor, ...]:
        outputs, measurement = self.endpoint.execute(_require_tensor_list(list(inputs)))
        self.last_measurement = measurement
        cuda_outputs = tuple(output.cuda(non_blocking=False) for output in outputs)
        if len(cuda_outputs) == 1:
            return cuda_outputs[0]
        return cuda_outputs

    @property
    def elapsed_ms(self) -> float:
        if self.last_measurement is None:
            raise RuntimeError("authority model has no completed measurement")
        return self.last_measurement.elapsed_ns / 1_000_000.0

    @property
    def observed_peak_bytes(self) -> int:
        values = [
            measurement.observed_peak_memory_bytes
            for measurement in self.endpoint.recorder.measurements
            if measurement.role == self.endpoint.role
        ]
        return max(values, default=0)


def _require_tensor_list(values: list[Any]) -> list[torch.Tensor]:
    if any(not isinstance(value, torch.Tensor) for value in values):
        raise TypeError("authority v1 accepts only positional tensor inputs")
    return values


__all__ = [
    "AuthorityEndpoint",
    "AuthorityRecorder",
    "CandidateAuthorityError",
    "CudaGlobalMemoryProbe",
    "RemoteAuthorityModel",
]
