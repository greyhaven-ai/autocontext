"""Generic HTTP webhook notifier."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from autocontext.notifications.base import NotificationEvent, Notifier

logger = logging.getLogger(__name__)


class HTTPNotifier(Notifier):
    """Sends notification events as JSON POST to a URL."""

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._url = url
        self._headers = headers or {}
        self._timeout = timeout

    def notify(self, event: NotificationEvent) -> None:
        try:
            payload = json.dumps({
                "type": event.type.value,
                "task_name": event.task_name,
                "task_id": event.task_id,
                "score": event.score,
                "previous_best": event.previous_best,
                "round_count": event.round_count,
                "cost_usd": event.cost_usd,
                "output_preview": event.output_preview[:500],
                "error": event.error,
                "summary": event.summary,
            }).encode("utf-8")

            req = urllib.request.Request(
                self._url,
                data=payload,
                headers={"Content-Type": "application/json", **self._headers},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=self._timeout)
        except Exception as exc:
            logger.warning("HTTP notification failed: %s", exc)
