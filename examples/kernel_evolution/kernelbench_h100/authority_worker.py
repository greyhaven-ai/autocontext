#!/usr/bin/env python3
"""Untrusted candidate-side worker for the accelerator authority protocol.

This process intentionally owns no private plan, reference, timing control,
resource observer, or report path.  Its responses are untrusted tensor bytes;
the evaluator independently validates them and owns all measurements.
"""

from __future__ import annotations

import argparse
import importlib.util
import socket
import sys
import time
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

import torch

from autocontext.kernel_evolution import (
    AuthorityRequest,
    AuthorityResponse,
    AuthorityWireError,
    artifact_digest,
    canonical_authority_digest,
    receive_authority_frame,
    send_authority_frame,
)
from autocontext.kernel_evolution.authority_tensor import (
    copy_tensor_to_device_preserving_abi,
    deserialize_tensor_list,
    serialize_tensor_list,
)


class _FunctionModel(torch.nn.Module):
    def __init__(self, function: Any) -> None:
        super().__init__()
        self._function = function

    def forward(self, *inputs: Any) -> Any:
        return self._function(*inputs)


def _load_module(path: Path) -> ModuleType:
    name = f"_autoctx_isolated_candidate_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("candidate source cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _build_model(module: ModuleType, entrypoint: str, init_inputs: list[Any]) -> Any:
    target = getattr(module, entrypoint, None)
    if target is None:
        raise AttributeError("candidate source omitted its declared entrypoint")
    if isinstance(target, type) and issubclass(target, torch.nn.Module):
        return target(*init_inputs).cuda().eval()
    if callable(target):
        return _FunctionModel(target).cuda().eval()
    raise TypeError("candidate entrypoint is not callable")


def _connect(path: Path, timeout_seconds: float) -> socket.socket:
    deadline = time.monotonic() + timeout_seconds
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.connect(str(path))
            connection.settimeout(timeout_seconds)
            return connection
        except OSError as exc:
            last_error = exc
            connection.close()
            time.sleep(0.01)
    raise TimeoutError("candidate authority could not connect to its evaluator socket") from last_error


def _response(
    request: AuthorityRequest,
    *,
    outcome: Literal["complete", "candidate_error", "oom", "protocol_error"],
    diagnostic_code: Literal[
        "none",
        "compile",
        "execution",
        "oom",
        "malformed_request",
        "malformed_output",
    ],
    outputs: list[Any],
) -> tuple[AuthorityResponse, bytes]:
    payload, manifest_digest = serialize_tensor_list(outputs, prefix="output")
    return (
        AuthorityResponse(
            request_id=request.request_id,
            session_nonce=request.session_nonce,
            sequence=request.sequence,
            role=request.role,
            artifact_digest=request.artifact_digest,
            outcome=outcome,
            output_manifest_digest=manifest_digest,
            payload_digest=canonical_authority_digest(payload),
            payload_bytes=len(payload),
            diagnostic_code=diagnostic_code,
        ),
        payload,
    )


def _run(args: argparse.Namespace) -> int:
    for support_path in args.support_path:
        sys.path.insert(0, str(Path(support_path)))
    source = Path(args.source)
    source_bytes = source.read_bytes()
    if artifact_digest(source_bytes, source_suffix=source.suffix, entrypoint=args.entrypoint) != args.artifact_digest:
        return 4
    connection = _connect(Path(args.connect), args.connect_timeout)
    model: Any | None = None
    session_nonce: str | None = None
    try:
        while True:
            request, payload = receive_authority_frame(connection, AuthorityRequest)
            if request.role != args.role or request.artifact_digest != args.artifact_digest:
                return 5
            if session_nonce is None:
                session_nonce = request.session_nonce
            elif request.session_nonce != session_nonce:
                return 6
            try:
                inputs, manifest_digest = deserialize_tensor_list(payload, prefix="input")
                if request.operation != "shutdown" and request.input_manifest_digest != manifest_digest:
                    raise AuthorityWireError("input manifest does not match the evaluator request")
                if request.operation == "initialize":
                    module = _load_module(source)
                    model = _build_model(
                        module,
                        args.entrypoint,
                        [copy_tensor_to_device_preserving_abi(value, device="cuda") for value in inputs],
                    )
                    response, response_payload = _response(
                        request,
                        outcome="complete",
                        diagnostic_code="none",
                        outputs=[],
                    )
                elif request.operation == "execute":
                    if model is None:
                        raise RuntimeError("candidate authority was not initialized")
                    cuda_inputs = [copy_tensor_to_device_preserving_abi(value, device="cuda") for value in inputs]
                    with torch.inference_mode():
                        raw_output = model(*cuda_inputs)
                    torch.cuda.synchronize()
                    values = list(raw_output) if isinstance(raw_output, (list, tuple)) else [raw_output]
                    if any(not isinstance(value, torch.Tensor) for value in values):
                        raise TypeError("candidate output must contain only tensors")
                    response, response_payload = _response(
                        request,
                        outcome="complete",
                        diagnostic_code="none",
                        outputs=[value.detach() for value in values],
                    )
                else:
                    response, response_payload = _response(
                        request,
                        outcome="complete",
                        diagnostic_code="none",
                        outputs=[],
                    )
                    send_authority_frame(connection, response, response_payload)
                    return 0
            except torch.cuda.OutOfMemoryError:
                response, response_payload = _response(
                    request,
                    outcome="oom",
                    diagnostic_code="oom",
                    outputs=[],
                )
            except AuthorityWireError:
                response, response_payload = _response(
                    request,
                    outcome="protocol_error",
                    diagnostic_code="malformed_request",
                    outputs=[],
                )
            except Exception:
                response, response_payload = _response(
                    request,
                    outcome="candidate_error",
                    diagnostic_code="compile" if request.operation == "initialize" else "execution",
                    outputs=[],
                )
            send_authority_frame(connection, response, response_payload)
    except (AuthorityWireError, OSError, TimeoutError):
        return 7
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--connect", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--entrypoint", required=True)
    parser.add_argument("--artifact-digest", required=True)
    parser.add_argument("--role", choices=("candidate", "incumbent"), required=True)
    parser.add_argument("--support-path", action="append", default=[])
    parser.add_argument("--connect-timeout", type=float, default=20.0)
    args = parser.parse_args()
    if args.connect_timeout <= 0:
        return 2
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
