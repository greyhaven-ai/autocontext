"""Notification system for MTS task results."""

from mts.notifications.base import Notifier, NotificationEvent, EventType
from mts.notifications.stdout import StdoutNotifier
from mts.notifications.http import HTTPNotifier
from mts.notifications.slack import SlackWebhookNotifier
from mts.notifications.callback import CallbackNotifier
from mts.notifications.composite import CompositeNotifier

__all__ = [
    "Notifier",
    "NotificationEvent",
    "EventType",
    "StdoutNotifier",
    "HTTPNotifier",
    "SlackWebhookNotifier",
    "CallbackNotifier",
    "CompositeNotifier",
]
