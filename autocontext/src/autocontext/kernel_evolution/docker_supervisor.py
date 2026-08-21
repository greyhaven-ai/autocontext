"""Authenticated PID-1 supervisor for Docker kernel benchmark adapters.

This module is intentionally standalone so its exact source can be mounted into
the worker image and executed with the image's Python interpreter.  The host
sends a one-run secret over the supervisor's private stdin.  Generated code
never inherits that descriptor or secret.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import hmac
import json
import os
import re
import select
import signal
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SUPERVISOR_PROTOCOL_VERSION = "autocontext.docker-supervisor/v1"
SUPERVISOR_STATUS_PREFIX = b"\x1eAUTOCONTEXT_DOCKER_SUPERVISOR_V1 "
SUPERVISOR_START_PREFIX = b"AUTOCONTEXT_START_V1 "
SUPERVISOR_ACK_PREFIX = b"AUTOCONTEXT_ACK_V1 "
MAX_SUPERVISOR_FRAME_BYTES = 2_048
MAX_SUPERVISOR_WIRE_BYTES = len(SUPERVISOR_STATUS_PREFIX) + MAX_SUPERVISOR_FRAME_BYTES + 2
_SECRET_BYTES = 32
_PR_SET_DUMPABLE = 4
_QUIESCE_SECONDS = 1.0
_IGNORED_SUPERVISOR_SIGNALS = (
    signal.SIGHUP,
    signal.SIGINT,
    signal.SIGQUIT,
    signal.SIGTERM,
    signal.SIGUSR1,
    signal.SIGUSR2,
)


@dataclass(frozen=True, slots=True)
class DockerSupervisorCompletion:
    adapter_returncode: int
    completed_at_ns: int
    report_size: int | None
    report_sha256: str | None
    supervisor_python: str = "/usr/local/bin/python"


def _canonical_completion(completion: DockerSupervisorCompletion) -> bytes:
    return json.dumps(
        {
            "adapter_returncode": completion.adapter_returncode,
            "completed_at_ns": completion.completed_at_ns,
            "report_sha256": completion.report_sha256,
            "report_size": completion.report_size,
            "schema_version": SUPERVISOR_PROTOCOL_VERSION,
            "supervisor_python": completion.supervisor_python,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _completion_mac(secret: bytes, completion: DockerSupervisorCompletion) -> str:
    return hmac.new(secret, b"completion\0" + _canonical_completion(completion), hashlib.sha256).hexdigest()


def encode_completion_frame(secret: bytes, completion: DockerSupervisorCompletion) -> bytes:
    payload = json.loads(_canonical_completion(completion))
    payload["mac"] = _completion_mac(secret, completion)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    return SUPERVISOR_STATUS_PREFIX + encoded + b"\n"


def decode_completion_frame(
    secret: bytes,
    frame: bytes,
    *,
    max_report_bytes: int,
) -> DockerSupervisorCompletion | None:
    if len(secret) != _SECRET_BYTES or not frame.startswith(SUPERVISOR_STATUS_PREFIX):
        return None
    raw = frame[len(SUPERVISOR_STATUS_PREFIX) :]
    if len(raw) > MAX_SUPERVISOR_FRAME_BYTES:
        return None
    try:
        payload = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict) or set(payload) != {
        "adapter_returncode",
        "completed_at_ns",
        "mac",
        "report_sha256",
        "report_size",
        "schema_version",
        "supervisor_python",
    }:
        return None
    if payload.get("schema_version") != SUPERVISOR_PROTOCOL_VERSION:
        return None
    returncode = payload.get("adapter_returncode")
    completed_at_ns = payload.get("completed_at_ns")
    report_size = payload.get("report_size")
    report_sha256 = payload.get("report_sha256")
    mac = payload.get("mac")
    supervisor_python = payload.get("supervisor_python")
    valid_returncode = type(returncode) is int and (
        0 <= returncode <= 255 or -signal.NSIG < returncode < 0
    )
    if (
        not valid_returncode
        or type(completed_at_ns) is not int
        or completed_at_ns < 1
        or not isinstance(mac, str)
        or re.fullmatch(r"[0-9a-f]{64}", mac) is None
        or not isinstance(supervisor_python, str)
        or not supervisor_python.startswith("/")
        or len(supervisor_python) > 512
        or any(character in supervisor_python for character in "\r\n\0")
    ):
        return None
    if report_size is None or report_sha256 is None:
        if report_size is not None or report_sha256 is not None:
            return None
    elif (
        type(report_size) is not int
        or not 0 <= report_size <= max_report_bytes
        or not isinstance(report_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", report_sha256) is None
    ):
        return None
    assert type(returncode) is int
    completion = DockerSupervisorCompletion(
        adapter_returncode=returncode,
        completed_at_ns=completed_at_ns,
        report_size=report_size,
        report_sha256=report_sha256,
        supervisor_python=supervisor_python,
    )
    return completion if hmac.compare_digest(mac, _completion_mac(secret, completion)) else None


def encode_start(secret: bytes) -> bytes:
    if len(secret) != _SECRET_BYTES:
        raise ValueError("Docker supervisor secret must contain exactly 32 bytes")
    return SUPERVISOR_START_PREFIX + secret.hex().encode("ascii") + b"\n"


def encode_ack(secret: bytes, completion: DockerSupervisorCompletion) -> bytes:
    digest = hashlib.sha256(_canonical_completion(completion)).digest()
    mac = hmac.new(secret, b"ack\0" + digest, hashlib.sha256).hexdigest().encode("ascii")
    return SUPERVISOR_ACK_PREFIX + mac + b"\n"


def _decode_start(line: bytes) -> bytes | None:
    if not line.startswith(SUPERVISOR_START_PREFIX):
        return None
    encoded = line[len(SUPERVISOR_START_PREFIX) :].strip()
    if len(encoded) != _SECRET_BYTES * 2 or re.fullmatch(rb"[0-9a-f]+", encoded) is None:
        return None
    return bytes.fromhex(encoded.decode("ascii"))


def _valid_ack(line: bytes, secret: bytes, completion: DockerSupervisorCompletion) -> bool:
    return hmac.compare_digest(line.strip(), encode_ack(secret, completion).strip())


class DockerSupervisorStatusCollector:
    """Bounded streaming parser for one authenticated supervisor frame."""

    def __init__(self, secret: bytes, *, max_report_bytes: int) -> None:
        self._secret = secret
        self._max_report_bytes = max_report_bytes
        self._buffer = bytearray()
        self._completion: DockerSupervisorCompletion | None = None
        self._frame: bytes | None = None
        self._lock = threading.Lock()
        self.ready = threading.Event()

    @property
    def completion(self) -> DockerSupervisorCompletion | None:
        with self._lock:
            return self._completion

    @property
    def authenticated_frame(self) -> bytes | None:
        with self._lock:
            return self._frame

    @property
    def buffered_bytes(self) -> int:
        with self._lock:
            return len(self._buffer)

    def feed(self, chunk: bytes) -> None:
        with self._lock:
            if self._completion is not None:
                return
            for offset in range(0, len(chunk), MAX_SUPERVISOR_WIRE_BYTES):
                self._buffer.extend(chunk[offset : offset + MAX_SUPERVISOR_WIRE_BYTES])
                while (newline := self._buffer.find(b"\n")) >= 0:
                    line = bytes(self._buffer[:newline])
                    del self._buffer[: newline + 1]
                    candidate = line[-MAX_SUPERVISOR_WIRE_BYTES:]
                    marker = candidate.rfind(SUPERVISOR_STATUS_PREFIX)
                    if marker < 0:
                        continue
                    frame = candidate[marker:]
                    decoded = decode_completion_frame(
                        self._secret,
                        frame,
                        max_report_bytes=self._max_report_bytes,
                    )
                    if decoded is not None:
                        self._completion = decoded
                        self._frame = frame + b"\n"
                        self.ready.set()
                        return
                self._bound_pending_buffer()

    def _bound_pending_buffer(self) -> None:
        if len(self._buffer) <= MAX_SUPERVISOR_WIRE_BYTES:
            return
        tail = self._buffer[-MAX_SUPERVISOR_WIRE_BYTES:]
        marker = tail.rfind(SUPERVISOR_STATUS_PREFIX)
        if marker >= 0:
            self._buffer[:] = tail[marker:]
            return
        overlap = min(len(SUPERVISOR_STATUS_PREFIX) - 1, len(tail))
        while overlap and tail[-overlap:] != SUPERVISOR_STATUS_PREFIX[:overlap]:
            overlap -= 1
        self._buffer[:] = tail[-overlap:] if overlap else b""


def _disable_ptrace() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    libc.prctl.restype = ctypes.c_int
    result = int(libc.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0))
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _kill_and_reap_descendants() -> bool:
    deadline = time.monotonic() + _QUIESCE_SECONDS
    while time.monotonic() < deadline:
        try:
            os.kill(-1, signal.SIGKILL)
        except ProcessLookupError:
            pass
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid == 0:
                break
        remaining = [item for item in os.listdir("/proc") if item.isdecimal() and item != str(os.getpid())]
        if not remaining:
            return True
        time.sleep(0.01)
    return False


def _report_identity(report_path: Path, max_report_bytes: int) -> tuple[int | None, str | None]:
    try:
        before = report_path.lstat()
    except FileNotFoundError:
        return None, None
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or before.st_size > max_report_bytes:
        return None, None
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(report_path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            return None, None
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(descriptor, min(64 * 1024, max_report_bytes + 1 - size)):
            size += len(chunk)
            if size > max_report_bytes:
                return None, None
            digest.update(chunk)
        after = os.fstat(descriptor)
        current = report_path.lstat()
        identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
        if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or identity != (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
            current.st_ctime_ns,
        ):
            return None, None
        return size, digest.hexdigest()
    except (FileNotFoundError, OSError):
        return None, None
    finally:
        os.close(descriptor)


def _normalized_exit_code(returncode: int) -> int:
    return returncode if returncode >= 0 else 128 - returncode


def normalized_adapter_exit_code(returncode: int) -> int:
    """Return the container exit status corresponding to a subprocess status."""

    if not (0 <= returncode <= 255 or -signal.NSIG < returncode < 0):
        raise ValueError("adapter returncode is outside the supported process status range")
    return _normalized_exit_code(returncode)


def _ignore_peer_signals() -> None:
    for signal_number in _IGNORED_SUPERVISOR_SIGNALS:
        signal.signal(signal_number, signal.SIG_IGN)


def _restore_adapter_signals() -> None:
    for signal_number in _IGNORED_SUPERVISOR_SIGNALS:
        signal.signal(signal_number, signal.SIG_DFL)


def _readline_before(descriptor: int, deadline_ns: int, pending: bytearray) -> bytes | None:
    while True:
        remaining_seconds = (deadline_ns - time.time_ns()) / 1_000_000_000
        if remaining_seconds <= 0:
            return None
        newline = pending.find(b"\n")
        if newline >= 0:
            line = bytes(pending[: newline + 1])
            del pending[: newline + 1]
            return line
        if len(pending) > MAX_SUPERVISOR_FRAME_BYTES:
            return None
        readable, _, _ = select.select([descriptor], [], [], remaining_seconds)
        if not readable:
            return None
        chunk = os.read(descriptor, MAX_SUPERVISOR_FRAME_BYTES + 1 - len(pending))
        if not chunk:
            return None
        pending.extend(chunk)


def _run_supervisor(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--max-report-bytes", type=int, required=True)
    parser.add_argument("--execution-deadline-ns", type=int, required=True)
    parser.add_argument("--hard-deadline-ns", type=int, required=True)
    parser.add_argument("adapter", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    adapter = list(args.adapter)
    if adapter[:1] == ["--"]:
        adapter = adapter[1:]
    if (
        os.getpid() != 1
        or not adapter
        or args.max_report_bytes < 1
        or args.execution_deadline_ns < 1
        or args.hard_deadline_ns <= args.execution_deadline_ns
    ):
        return 125
    try:
        _disable_ptrace()
        _ignore_peer_signals()
        stdin_descriptor = sys.stdin.buffer.fileno()
        pending = bytearray()
        start_line = _readline_before(stdin_descriptor, args.execution_deadline_ns, pending)
        secret = _decode_start(start_line or b"")
        if secret is None:
            return 125
        child = subprocess.Popen(  # noqa: S603
            adapter,
            stdin=subprocess.DEVNULL,
            stdout=None,
            stderr=None,
            close_fds=True,
            start_new_session=True,
            preexec_fn=_restore_adapter_signals,
        )
        remaining_seconds = (args.execution_deadline_ns - time.time_ns()) / 1_000_000_000
        if remaining_seconds <= 0:
            _kill_and_reap_descendants()
            return 124
        try:
            returncode = child.wait(timeout=remaining_seconds)
        except subprocess.TimeoutExpired:
            _kill_and_reap_descendants()
            return 124
        completed_at_ns = time.time_ns()
        if completed_at_ns >= args.execution_deadline_ns:
            _kill_and_reap_descendants()
            return 124
        if not _kill_and_reap_descendants():
            return 125
        report_size, report_sha256 = _report_identity(args.report, args.max_report_bytes)
        if time.time_ns() >= args.hard_deadline_ns:
            return 124
        completion = DockerSupervisorCompletion(
            adapter_returncode=returncode,
            completed_at_ns=completed_at_ns,
            report_size=report_size,
            report_sha256=report_sha256,
            supervisor_python=str(Path(sys.executable).resolve(strict=True)),
        )
        sys.stdout.buffer.write(b"\n" + encode_completion_frame(secret, completion))
        sys.stdout.buffer.flush()
        while line := _readline_before(stdin_descriptor, args.hard_deadline_ns, pending):
            if _valid_ack(line, secret, completion):
                return normalized_adapter_exit_code(returncode)
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
        return 125
    return 125


def _entrypoint(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] != "--supervise":
        return 2
    return _run_supervisor(argv[2:])


__all__ = [
    "DockerSupervisorCompletion",
    "DockerSupervisorStatusCollector",
    "MAX_SUPERVISOR_WIRE_BYTES",
    "SUPERVISOR_PROTOCOL_VERSION",
    "encode_ack",
    "encode_completion_frame",
    "encode_start",
    "normalized_adapter_exit_code",
]


if __name__ == "__main__":  # pragma: no cover - exercised inside the worker container
    raise SystemExit(_entrypoint(sys.argv))
