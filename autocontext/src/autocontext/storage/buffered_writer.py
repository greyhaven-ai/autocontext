"""Buffered artifact writer — background thread for non-critical I/O.

Queues filesystem writes and processes them in a daemon thread.
Critical writes (playbook, SQLite, recovery markers) should NOT use
this writer — they must remain synchronous.

If ``start()`` is never called, all methods fall back to synchronous
writes so the writer is safe to use without threading.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

_SENTINEL = object()


@dataclass(slots=True)
class _WriteItem:
    path: Path
    content: str
    mode: Literal["write", "append"]


class BufferedWriter:
    """Thread-safe buffered file writer.

    Usage::

        writer = BufferedWriter()
        writer.start()
        writer.write_text(path, content)
        writer.flush()   # blocks until queue empty
        writer.shutdown() # flushes + stops thread
    """

    def __init__(self) -> None:
        self._queue: queue.Queue[_WriteItem | object] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._started = False

    def start(self) -> None:
        """Start the background writer thread."""
        if self._started:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="buffered-writer")
        self._thread.start()
        self._started = True

    def _run(self) -> None:
        """Background thread: process write items until sentinel."""
        while True:
            item = self._queue.get()
            try:
                if item is _SENTINEL:
                    break
                if not isinstance(item, _WriteItem):
                    logger.error("unexpected item type in buffered writer queue: %r", type(item))
                    continue
                self._execute(item)
            except Exception:
                logger.exception("buffered write failed: %s", getattr(item, "path", "?"))
            finally:
                self._queue.task_done()

    @staticmethod
    def _execute(item: _WriteItem) -> None:
        """Perform a single write operation."""
        item.path.parent.mkdir(parents=True, exist_ok=True)
        if item.mode == "append":
            with item.path.open("a", encoding="utf-8") as f:
                f.write(item.content)
        else:
            item.path.write_text(item.content, encoding="utf-8")

    def write_text(self, path: Path, content: str) -> None:
        """Queue a text write (or write synchronously if not started)."""
        item = _WriteItem(path=path, content=content, mode="write")
        if not self._started:
            self._execute(item)
            return
        self._queue.put(item)

    def write_json(self, path: Path, payload: dict[str, object]) -> None:
        """Queue a JSON write."""
        content = json.dumps(payload, indent=2, sort_keys=True)
        self.write_text(path, content)

    def append_text(self, path: Path, content: str) -> None:
        """Queue a text append (or append synchronously if not started)."""
        item = _WriteItem(path=path, content=content, mode="append")
        if not self._started:
            self._execute(item)
            return
        self._queue.put(item)

    def flush(self) -> None:
        """Block until all queued writes are processed."""
        if not self._started:
            return
        self._queue.join()

    def shutdown(self) -> None:
        """Flush remaining writes and stop the background thread."""
        if not self._started:
            return
        self._queue.put(_SENTINEL)
        if self._thread is not None:
            self._thread.join(timeout=30)
        self._started = False
