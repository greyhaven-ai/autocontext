"""Callback notifier — calls a user-provided function."""

from __future__ import annotations

import logging
from collections.abc import Callable

from autocontext.notifications.base import NotificationEvent, Notifier

logger = logging.getLogger(__name__)


class CallbackNotifier(Notifier):
    """Calls a user-provided function with each event."""

    def __init__(self, fn: Callable[[NotificationEvent], None]) -> None:
        self._fn = fn

    def notify(self, event: NotificationEvent) -> None:
        try:
            self._fn(event)
        except Exception as exc:
            logger.warning("Callback notification failed: %s", exc)
