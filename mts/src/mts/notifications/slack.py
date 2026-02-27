"""Slack incoming webhook notifier."""

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error

from mts.notifications.base import EventType, Notifier, NotificationEvent

logger = logging.getLogger(__name__)


class SlackWebhookNotifier(Notifier):
    """Sends notifications to a Slack incoming webhook.

    Formats events as Slack-friendly messages with emoji and structure.
    """

    def __init__(self, webhook_url: str, channel: str | None = None, timeout: float = 10.0) -> None:
        self._url = webhook_url
        self._channel = channel
        self._timeout = timeout

    def notify(self, event: NotificationEvent) -> None:
        try:
            blocks = self._format_blocks(event)
            payload: dict = {"blocks": blocks}
            if self._channel:
                payload["channel"] = self._channel

            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self._url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=self._timeout)
        except Exception as exc:
            logger.warning("Slack notification failed: %s", exc)

    def _format_blocks(self, event: NotificationEvent) -> list[dict]:
        emoji = {
            EventType.THRESHOLD_MET: "✅",
            EventType.REGRESSION: "⚠️",
            EventType.COMPLETION: "📋",
            EventType.FAILURE: "❌",
        }.get(event.type, "📌")

        header = f"{emoji} *MTS: {event.task_name}*"
        blocks: list[dict] = [
            {"type": "section", "text": {"type": "mrkdwn", "text": header}},
            {"type": "section", "text": {"type": "mrkdwn", "text": event.summary}},
        ]

        fields = []
        if event.score is not None:
            fields.append({"type": "mrkdwn", "text": f"*Score:* {event.score:.2f}"})
        if event.round_count:
            fields.append({"type": "mrkdwn", "text": f"*Rounds:* {event.round_count}"})
        if event.cost_usd is not None:
            fields.append({"type": "mrkdwn", "text": f"*Cost:* ${event.cost_usd:.4f}"})
        if event.previous_best is not None:
            fields.append({"type": "mrkdwn", "text": f"*Previous best:* {event.previous_best:.2f}"})

        if fields:
            blocks.append({"type": "section", "fields": fields})

        if event.output_preview:
            preview = event.output_preview[:300]
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"```{preview}```"},
            })

        return blocks
