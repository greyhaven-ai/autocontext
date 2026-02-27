"""Stdout notifier — prints events to console."""

from __future__ import annotations

import logging

from mts.notifications.base import Notifier, NotificationEvent

logger = logging.getLogger(__name__)


class StdoutNotifier(Notifier):
    """Prints notification events to stdout/logging."""

    def __init__(self, use_logger: bool = False) -> None:
        self._use_logger = use_logger

    def notify(self, event: NotificationEvent) -> None:
        try:
            msg = event.summary
            if self._use_logger:
                logger.info("MTS notification: %s", msg)
            else:
                print(f"[MTS] {msg}")
        except Exception:
            pass  # Fire and forget
