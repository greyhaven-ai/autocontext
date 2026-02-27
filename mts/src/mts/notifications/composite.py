"""Composite notifier — fans out to multiple notifiers with event filtering."""

from __future__ import annotations

import logging

from mts.notifications.base import EventType, Notifier, NotificationEvent

logger = logging.getLogger(__name__)


class CompositeNotifier(Notifier):
    """Sends events to multiple notifiers with optional event type filtering."""

    def __init__(
        self,
        notifiers: list[Notifier],
        notify_on: set[EventType] | None = None,
    ) -> None:
        self._notifiers = notifiers
        self._notify_on = notify_on  # None = all events

    def notify(self, event: NotificationEvent) -> None:
        if self._notify_on and event.type not in self._notify_on:
            return

        for notifier in self._notifiers:
            try:
                notifier.notify(event)
            except Exception as exc:
                logger.warning("Notifier %s failed: %s", type(notifier).__name__, exc)
