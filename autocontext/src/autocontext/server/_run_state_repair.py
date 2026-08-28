"""Durable repair for interactive runs interrupted outside normal unwinding."""

from __future__ import annotations

import logging

from autocontext.config import AppSettings
from autocontext.storage import SQLiteStore


def repair_interrupted_run_state(
    settings: AppSettings,
    actual_run_id: str,
    logger: logging.Logger,
) -> None:
    if not settings.db_path.exists():
        return
    try:
        SQLiteStore(settings.db_path).mark_running_run_and_generations_failed(
            actual_run_id
        )
    except Exception:
        logger.exception("Run %s interrupted-state repair failed", actual_run_id)
