"""Switch SQLite databases to WAL journal mode under concurrent startup."""

from __future__ import annotations

import sqlite3
import time

# sqlite3.connect()'s default busy timeout, for connections opened without one.
DEFAULT_TIMEOUT_SECONDS = 5.0
_MAX_RETRY_DELAY_SECONDS = 0.05
# In-memory databases cannot use WAL; SQLite keeps reporting "memory" for them.
_ACCEPTED_JOURNAL_MODES = frozenset({"wal", "memory"})


def enable_wal(connection: sqlite3.Connection, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
    """Switch the database to WAL, tolerating a concurrent writer.

    The conversion needs a brief exclusive lock, and unlike ordinary statements
    `PRAGMA journal_mode` returns SQLITE_BUSY *without* invoking the busy
    handler, so neither `sqlite3.connect(timeout=...)` nor `PRAGMA busy_timeout`
    covers it. Retry here for up to ``timeout_seconds``, which callers set to
    their connection's busy timeout. Losing the race is a success rather than
    an error: the journal mode lives in the database header, so once any
    initializer wins, every later connection already sees WAL.
    """
    deadline = time.monotonic() + timeout_seconds
    delay = 0.001
    while True:
        try:
            row = connection.execute("PRAGMA journal_mode=WAL").fetchone()
        except sqlite3.OperationalError as error:
            message = str(error).lower()
            if ("locked" not in message and "busy" not in message) or time.monotonic() >= deadline:
                raise
        else:
            mode = str(row[0]).lower() if row is not None else ""
            if mode in _ACCEPTED_JOURNAL_MODES:
                return
            if time.monotonic() >= deadline:
                raise sqlite3.OperationalError(f"could not switch to WAL journal mode; database reports {mode!r}")
        time.sleep(delay)
        delay = min(delay * 2, _MAX_RETRY_DELAY_SECONDS)
