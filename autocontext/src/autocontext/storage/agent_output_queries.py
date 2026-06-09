"""SQLite query helpers for agent outputs.

Extracted from ``sqlite_store.py`` to keep that module under its size cap. These are pure
functions over a ``sqlite3.Connection`` (whose ``row_factory`` the caller sets), so they
stay easy to test and reuse without growing the store module.
"""

from __future__ import annotations

import sqlite3
from typing import Any


def latest_agent_outputs(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    """Every agent's output text for the most recent generation of a run.

    Shape: ``{"generation": int | None, "outputs": [{"role": str, "content": str}, ...]}``.
    Used by the cowork GUI to show the live candidate the loop is producing.
    """
    head = conn.execute(
        "SELECT MAX(generation_index) AS g FROM agent_outputs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    generation = head["g"] if head and head["g"] is not None else None
    if generation is None:
        return {"generation": None, "outputs": []}
    rows = conn.execute(
        """
        SELECT role, content
        FROM agent_outputs
        WHERE run_id = ? AND generation_index = ?
        ORDER BY rowid
        """,
        (run_id, generation),
    ).fetchall()
    return {"generation": generation, "outputs": [dict(r) for r in rows]}
