"""Bounded JSON IPC and child-side proxies for interactive run processes."""

from __future__ import annotations

import importlib
import json
import logging
import math
import os
import select
import struct
import threading
import time
from collections.abc import Callable
from multiprocessing.connection import Connection
from typing import Any

from autocontext.config import AppSettings
from autocontext.loop.generation_runner import GenerationRunner

logger = logging.getLogger(__name__)

_MAX_RUN_IPC_BYTES = 4 * 1024 * 1024
_MAX_EVENT_IPC_BYTES = 256 * 1024
_CONTROL_RESPONSE_TIMEOUT_SECONDS = 0.5
_MAX_IPC_FRAMES_PER_TICK = 64
_MAX_IPC_READ_BYTES_PER_TICK = 2 * _MAX_RUN_IPC_BYTES


class _RunProcessProtocolError(RuntimeError):
    """Raised when the spawned runner's bounded JSON channel is invalid."""


def _send_json_message(connection: Connection, message: dict[str, Any]) -> None:
    encoded = _encode_json_message(message)
    connection.send_bytes(encoded)


def _encode_json_message(message: dict[str, Any]) -> bytes:
    encoded = json.dumps(
        message,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > _MAX_RUN_IPC_BYTES:
        raise ValueError("interactive run IPC message exceeds the size limit")
    return encoded


def _send_json_message_with_deadline(
    connection: Connection,
    message: dict[str, Any],
    *,
    timeout_seconds: float,
) -> None:
    """Send one parent response without letting a non-reading child stall cleanup."""
    encoded = _encode_json_message(message)
    if os.name != "posix":
        _send_windows_message_with_deadline(
            connection,
            encoded,
            timeout_seconds=timeout_seconds,
        )
        return

    framed = memoryview(struct.pack("!i", len(encoded)) + encoded)
    deadline = time.monotonic() + timeout_seconds
    try:
        fd = connection.fileno()
        os.set_blocking(fd, False)
    except (AttributeError, OSError, ValueError) as exc:
        raise OSError("interactive run IPC response channel is unavailable") from exc
    try:
        while framed:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("interactive run controller response timed out")
            _readable, writable, _exceptional = select.select([], [fd], [], remaining)
            if not writable:
                raise TimeoutError("interactive run controller response timed out")
            try:
                written = os.write(fd, framed)
            except BlockingIOError:
                continue
            if written <= 0:
                raise BrokenPipeError("interactive run controller response channel closed")
            framed = framed[written:]
    finally:
        try:
            os.set_blocking(fd, True)
        except OSError:
            pass


def _send_windows_message_with_deadline(
    connection: Connection,
    encoded: bytes,
    *,
    timeout_seconds: float,
    winapi_module: Any | None = None,
) -> None:
    """Bound one overlapped Windows named-pipe write without a helper thread."""
    if winapi_module is None:
        try:
            api = importlib.import_module("_winapi")
        except ImportError as exc:  # pragma: no cover - platform invariant
            raise OSError("Windows overlapped pipe support is unavailable") from exc
    else:
        api = winapi_module

    overlapped, error = api.WriteFile(connection.fileno(), encoded, overlapped=True)
    if error == api.ERROR_IO_PENDING:
        wait_result = api.WaitForMultipleObjects(
            [overlapped.event],
            False,
            max(1, math.ceil(timeout_seconds * 1_000)),
        )
        if wait_result == api.WAIT_TIMEOUT:
            _cancel_and_drain_overlapped(overlapped)
            raise TimeoutError("interactive run controller response timed out")
        if wait_result != api.WAIT_OBJECT_0:
            _cancel_and_drain_overlapped(overlapped)
            raise OSError("Windows controller response wait failed")
    written, error = overlapped.GetOverlappedResult(True)
    if error != 0 or written != len(encoded):
        raise OSError("Windows controller response write failed")


def _cancel_and_drain_overlapped(overlapped: Any) -> None:
    try:
        overlapped.cancel()
    except OSError:
        pass
    finally:
        try:
            overlapped.GetOverlappedResult(True)
        except OSError:
            pass


def _receive_json_message(connection: Connection) -> dict[str, Any]:
    return _decode_json_payload(connection.recv_bytes(_MAX_RUN_IPC_BYTES))


def _decode_json_payload(raw: bytes) -> dict[str, Any]:
    """Decode one strict JSON object, rejecting every non-finite number."""

    def reject_nonstandard_constant(value: str) -> Any:
        raise ValueError(f"non-finite JSON constant {value!r} is not allowed")

    def parse_finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("non-finite JSON number is not allowed")
        return parsed

    payload = json.loads(
        raw,
        parse_constant=reject_nonstandard_constant,
        parse_float=parse_finite_float,
    )
    if not isinstance(payload, dict):
        raise ValueError("interactive run IPC message must be an object")
    return payload


class _IncrementalConnectionReader:
    """Read bounded ``Connection.send_bytes`` frames without blocking on partial data."""

    def __init__(
        self,
        connection: Connection,
        *,
        max_frame_bytes: int = _MAX_RUN_IPC_BYTES,
    ) -> None:
        if not 1 <= max_frame_bytes <= _MAX_RUN_IPC_BYTES:
            raise ValueError("invalid incremental IPC frame limit")
        self._connection = connection
        self._max_frame_bytes = max_frame_bytes
        self._buffer = bytearray()
        self._eof = False

    def receive_available(
        self,
        *,
        read_from_fd: bool = True,
        max_messages: int = _MAX_IPC_FRAMES_PER_TICK,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Return complete frames currently available and whether the channel is open."""
        if not 1 <= max_messages <= _MAX_IPC_FRAMES_PER_TICK:
            raise ValueError("invalid incremental IPC message limit")
        messages = self._extract_messages(max_messages)
        if self._eof and not self._buffer:
            return messages, False
        if not read_from_fd:
            if self._eof and self._buffer and not self.has_buffered_frame:
                raise _RunProcessProtocolError("interactive run IPC channel ended mid-frame")
            return messages, not self._eof or bool(self._buffer)
        if self._eof:
            return messages, bool(self._buffer)
        if len(messages) >= max_messages:
            return messages, True
        if os.name != "posix":
            try:
                raw = self._connection.recv_bytes(self._max_frame_bytes)
                return [_decode_json_payload(raw)], True
            except EOFError:
                self._eof = True
                return [], False
            except (
                json.JSONDecodeError,
                OSError,
                RecursionError,
                UnicodeDecodeError,
                ValueError,
            ) as exc:
                raise _RunProcessProtocolError(
                    "interactive run IPC frame contains invalid JSON"
                ) from exc

        try:
            fd = self._connection.fileno()
            os.set_blocking(fd, False)
        except (AttributeError, OSError, ValueError) as exc:
            raise _RunProcessProtocolError("interactive run IPC channel cannot be read incrementally") from exc

        bytes_read = 0
        try:
            while bytes_read < _MAX_IPC_READ_BYTES_PER_TICK:
                buffer_room = self._max_frame_bytes + 4 - len(self._buffer)
                if buffer_room <= 0:
                    break
                try:
                    chunk = os.read(
                        fd,
                        min(
                            65_536,
                            _MAX_IPC_READ_BYTES_PER_TICK - bytes_read,
                            buffer_room,
                        ),
                    )
                except BlockingIOError:
                    break
                if not chunk:
                    self._eof = True
                    break
                bytes_read += len(chunk)
                self._buffer.extend(chunk)
                if len(messages) < max_messages:
                    messages.extend(
                        self._extract_messages(max_messages - len(messages))
                    )
        finally:
            try:
                os.set_blocking(fd, True)
            except OSError:
                pass

        if len(messages) < max_messages:
            messages.extend(self._extract_messages(max_messages - len(messages)))
        if len(self._buffer) > self._max_frame_bytes + 4:
            raise _RunProcessProtocolError("interactive run IPC frame exceeds the size limit")
        if self._eof and self._buffer and not self.has_buffered_frame:
            raise _RunProcessProtocolError("interactive run IPC channel ended mid-frame")
        return messages, not self._eof or bool(self._buffer)

    @property
    def has_buffered_frame(self) -> bool:
        if len(self._buffer) < 4:
            return False
        frame_size = int(struct.unpack("!i", self._buffer[:4])[0])
        return frame_size < 0 or frame_size > self._max_frame_bytes or len(self._buffer) >= 4 + frame_size

    @property
    def has_buffered_bytes(self) -> bool:
        return bool(self._buffer)

    def _extract_messages(self, limit: int) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        while limit > 0 and len(self._buffer) >= 4:
            frame_size = int(struct.unpack("!i", self._buffer[:4])[0])
            if frame_size < 0 or frame_size > self._max_frame_bytes:
                raise _RunProcessProtocolError("interactive run IPC frame has an invalid size")
            frame_end = 4 + frame_size
            if len(self._buffer) < frame_end:
                break
            raw = bytes(self._buffer[4:frame_end])
            del self._buffer[:frame_end]
            try:
                messages.append(_decode_json_payload(raw))
            except (json.JSONDecodeError, RecursionError, UnicodeDecodeError, ValueError) as exc:
                raise _RunProcessProtocolError("interactive run IPC frame contains invalid JSON") from exc
            limit -= 1
        return messages


class _ProcessLoopController:
    """Child-side controller proxy over bounded JSON rather than pickle."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._lock = threading.Lock()
        self._next_token: str | None = None

    def _call(self, operation: str, *args: Any) -> Any:
        with self._lock:
            _send_json_message(
                self._connection,
                {
                    "type": "control",
                    "operation": operation,
                    "args": list(args),
                    "token": self._next_token,
                },
            )
            response = _receive_json_message(self._connection)
            if response.get("type") != "control_result" or not isinstance(
                response.get("ok"), bool
            ):
                raise RuntimeError("interactive controller returned an invalid response")
            next_token = response.get("next_token")
            if not isinstance(next_token, str):
                raise RuntimeError(
                    "interactive controller returned an invalid protocol token"
                )
            self._next_token = next_token
        if not response["ok"]:
            error_type = response.get("error_type", "RuntimeError")
            message = response.get("message", "interactive controller call failed")
            raise RuntimeError(f"{error_type}: {message}")
        return response.get("value")

    def wait_if_paused(self) -> None:
        while self._call("is_paused") is True:
            time.sleep(0.05)

    def stop_requested(self) -> bool:
        return self._call("stop_requested") is True

    def stop_details(self) -> tuple[str | None, str | None]:
        value = self._call("stop_details")
        if not isinstance(value, list) or len(value) != 2:
            raise RuntimeError("interactive controller returned invalid stop details")
        command_id, reason = value
        if command_id is not None and not isinstance(command_id, str):
            raise RuntimeError("interactive controller returned an invalid command id")
        if reason is not None and not isinstance(reason, str):
            raise RuntimeError("interactive controller returned an invalid stop reason")
        return command_id, reason

    def take_hint(self) -> str | None:
        value = self._call("take_hint")
        if value is not None and not isinstance(value, str):
            raise RuntimeError("interactive controller returned an invalid hint")
        return value

    def take_gate_override(self) -> str | None:
        value = self._call("take_gate_override")
        if value is not None and not isinstance(value, str):
            raise RuntimeError("interactive controller returned an invalid gate override")
        return value

    def poll_chat(self) -> tuple[str, str] | None:
        value = self._call("poll_chat")
        if value is None:
            return None
        if not isinstance(value, list) or len(value) != 2 or not all(isinstance(item, str) for item in value):
            raise RuntimeError("interactive controller returned an invalid chat request")
        return value[0], value[1]

    def respond_chat(self, role: str, response: str) -> None:
        self._call("respond_chat", role, response)


class _ProcessEventEmitter:
    """Child-side event emitter whose parent relay owns disk and subscribers."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._lock = threading.Lock()
        self._closed = False

    def _send_message(self, message: dict[str, Any]) -> None:
        if (
            message.get("type") == "event"
            and len(_encode_json_message(message)) > _MAX_EVENT_IPC_BYTES
        ):
            raise ValueError("interactive run event exceeds the size limit")
        _send_json_message(self._connection, message)

    def emit(
        self,
        event: str,
        payload: dict[str, Any],
        channel: str = "generation",
    ) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("interactive event channel is closed")
            self._send_message(
                {
                    "type": "event",
                    "event": event,
                    "payload": payload,
                    "channel": channel,
                }
            )

    def send_result(
        self,
        *,
        run_id: str,
        best_score: float | None = None,
        error: BaseException | None = None,
    ) -> None:
        with self._lock:
            if self._closed:
                return
            _send_run_result(
                self._connection,
                run_id=run_id,
                best_score=best_score,
                error=error,
                send_message=self._send_message,
            )
            self._closed = True

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._connection.close()


def _send_run_result(
    connection: Connection,
    *,
    run_id: str,
    best_score: float | None = None,
    error: BaseException | None = None,
    send_message: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    message: dict[str, Any] = {
        "type": "result",
        "run_id": run_id,
        "ok": error is None,
    }
    if error is None:
        message["best_score"] = best_score
    else:
        message["error_type"] = type(error).__name__[:128]
        message["message"] = str(error)[:2_000]
    try:
        if send_message is None:
            _send_json_message(connection, message)
        else:
            send_message(message)
    except (BrokenPipeError, OSError, ValueError):
        pass


def _run_generation_process(
    settings_payload: dict[str, Any],
    scenario: str,
    generations: int,
    minimum_generations: int,
    actual_run_id: str,
    require_playbook_approval: bool,
    control_connection: Connection,
    event_connection: Connection,
) -> None:
    """Spawn target: run generation on a single-threaded process main thread."""
    if os.name == "posix":
        try:
            os.setsid()
        except OSError as exc:
            _send_run_result(event_connection, run_id=actual_run_id, error=exc)
            control_connection.close()
            event_connection.close()
            return

    event_emitter: _ProcessEventEmitter | None = None
    try:
        settings = AppSettings.model_validate(settings_payload)
        runner = GenerationRunner(settings)
        runner.artifacts.shutdown_writer()
        if (
            (settings.rlm_enabled and settings.rlm_backend == "exec")
            or settings.code_strategies_enabled
            or settings.harness_validators_enabled
            or settings.policy_refinement_enabled
        ):
            from autocontext.execution.isolated_python import (
                IsolationUnavailableError,
                local_isolation_available,
            )

            if not local_isolation_available():
                raise IsolationUnavailableError(
                    "interactive generated-code execution requires a single-threaded spawned runner process"
                )
        runner.controller = _ProcessLoopController(control_connection)  # type: ignore[assignment]
        event_emitter = _ProcessEventEmitter(event_connection)
        runner.events = event_emitter  # type: ignore[assignment]
        summary = runner.run(
            scenario_name=scenario,
            generations=generations,
            minimum_generations=minimum_generations,
            run_id=actual_run_id,
            require_playbook_approval=require_playbook_approval,
        )
        event_emitter.send_result(
            run_id=summary.run_id,
            best_score=summary.best_score,
        )
    except BaseException as exc:
        logger.exception("Run %s failed", actual_run_id)
        if event_emitter is None:
            _send_run_result(event_connection, run_id=actual_run_id, error=exc)
        else:
            event_emitter.send_result(run_id=actual_run_id, error=exc)
    finally:
        control_connection.close()
        if event_emitter is None:
            event_connection.close()
        else:
            event_emitter.close()
