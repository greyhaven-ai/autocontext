"""Notification system for autocontext task results."""

from autocontext.notifications.base import EventType, NotificationEvent, Notifier
from autocontext.notifications.callback import CallbackNotifier
from autocontext.notifications.composite import CompositeNotifier
from autocontext.notifications.http import HTTPNotifier
from autocontext.notifications.slack import SlackWebhookNotifier
from autocontext.notifications.stdout import StdoutNotifier

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
