"""TraceSink protocol + FileSink implementation.

Spec §4.1 + §7.2. No atexit by default; ``register_atexit=True`` opts in.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import time
from pathlib import Path
from threading import Lock
from typing import Any, Literal, Protocol, runtime_checkable

_logger = logging.getLogger("autocontext.integrations.openai.FileSink")


@runtime_checkable
class TraceSink(Protocol):
    def add(self, trace: dict[str, Any]) -> None: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...


class FileSink:
    """Batched JSONL trace sink.

    Buffers traces in memory; flushes on ``batch_size`` or ``flush_interval_seconds``
    elapsed since the last write (whichever comes first). Writes are append-only
    with fsync.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        batch_size: int = 64,
        flush_interval_seconds: float = 5.0,
        on_error: Literal["raise", "log-and-drop"] = "raise",
        register_atexit: bool = False,
    ) -> None:
        self._path = Path(path)
        self._batch_size = batch_size
        self._flush_interval_seconds = flush_interval_seconds
        self._on_error = on_error
        self._buffer: list[dict[str, Any]] = []
        self._lock = Lock()
        self._last_flush_at = time.monotonic()
        self._closed = False
        if register_atexit:
            atexit.register(self._atexit_handler)

    def add(self, trace: dict[str, Any]) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("FileSink is closed")
            self._buffer.append(trace)
            if len(self._buffer) >= self._batch_size:
                self._flush_locked()
                return
            if time.monotonic() - self._last_flush_at >= self._flush_interval_seconds:
                self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._flush_locked()
            self._closed = True

    def _flush_locked(self) -> None:
        if not self._buffer:
            self._last_flush_at = time.monotonic()
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                for trace in self._buffer:
                    f.write(json.dumps(trace, separators=(",", ":"), sort_keys=True))
                    f.write("\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError as exc:
            if self._on_error == "raise":
                raise
            _logger.warning("FileSink flush failed: %s", exc)
        finally:
            self._buffer.clear()
            self._last_flush_at = time.monotonic()

    def _atexit_handler(self) -> None:
        try:
            self.close()
        except Exception:  # pragma: no cover
            pass
