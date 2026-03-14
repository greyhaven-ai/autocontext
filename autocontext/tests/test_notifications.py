"""Tests for the notification system."""

from __future__ import annotations

import json
from unittest.mock import patch

from autocontext.notifications.base import EventType, NotificationEvent
from autocontext.notifications.callback import CallbackNotifier
from autocontext.notifications.composite import CompositeNotifier
from autocontext.notifications.http import HTTPNotifier
from autocontext.notifications.slack import SlackWebhookNotifier
from autocontext.notifications.stdout import StdoutNotifier

# ---------------------------------------------------------------------------
# NotificationEvent
# ---------------------------------------------------------------------------

class TestNotificationEvent:
    def test_threshold_met_summary(self):
        e = NotificationEvent(type=EventType.THRESHOLD_MET, task_name="test", score=0.95, round_count=3)
        assert "0.95" in e.summary
        assert "met threshold" in e.summary

    def test_regression_summary(self):
        e = NotificationEvent(type=EventType.REGRESSION, task_name="test", score=0.60, previous_best=0.85)
        assert "0.85" in e.summary
        assert "0.60" in e.summary

    def test_completion_summary(self):
        e = NotificationEvent(type=EventType.COMPLETION, task_name="test", score=0.80, round_count=5)
        assert "completed" in e.summary
        assert "0.80" in e.summary

    def test_failure_summary(self):
        e = NotificationEvent(type=EventType.FAILURE, task_name="test", error="API timeout")
        assert "failed" in e.summary
        assert "API timeout" in e.summary

    def test_failure_truncates_error(self):
        e = NotificationEvent(type=EventType.FAILURE, task_name="test", error="x" * 200)
        assert len(e.summary) < 250


# ---------------------------------------------------------------------------
# StdoutNotifier
# ---------------------------------------------------------------------------

class TestStdoutNotifier:
    def test_prints(self, capsys):
        n = StdoutNotifier()
        e = NotificationEvent(type=EventType.COMPLETION, task_name="test", score=0.80, round_count=2)
        n.notify(e)
        captured = capsys.readouterr()
        assert "[autocontext]" in captured.out
        assert "test" in captured.out

    def test_logger_mode(self):
        n = StdoutNotifier(use_logger=True)
        e = NotificationEvent(type=EventType.COMPLETION, task_name="test", score=0.80)
        # Should not raise
        n.notify(e)


# ---------------------------------------------------------------------------
# CallbackNotifier
# ---------------------------------------------------------------------------

class TestCallbackNotifier:
    def test_calls_function(self):
        events = []
        n = CallbackNotifier(events.append)
        e = NotificationEvent(type=EventType.THRESHOLD_MET, task_name="test", score=0.95)
        n.notify(e)
        assert len(events) == 1
        assert events[0].score == 0.95

    def test_swallows_errors(self):
        def bad_fn(e):
            raise RuntimeError("boom")
        n = CallbackNotifier(bad_fn)
        e = NotificationEvent(type=EventType.FAILURE, task_name="test")
        n.notify(e)  # Should not raise


# ---------------------------------------------------------------------------
# CompositeNotifier
# ---------------------------------------------------------------------------

class TestCompositeNotifier:
    def test_fans_out(self):
        events_a, events_b = [], []
        a = CallbackNotifier(events_a.append)
        b = CallbackNotifier(events_b.append)
        composite = CompositeNotifier([a, b])

        e = NotificationEvent(type=EventType.COMPLETION, task_name="test")
        composite.notify(e)
        assert len(events_a) == 1
        assert len(events_b) == 1

    def test_filters_events(self):
        events = []
        n = CallbackNotifier(events.append)
        composite = CompositeNotifier([n], notify_on={EventType.THRESHOLD_MET})

        composite.notify(NotificationEvent(type=EventType.COMPLETION, task_name="t"))
        assert len(events) == 0  # Filtered

        composite.notify(NotificationEvent(type=EventType.THRESHOLD_MET, task_name="t"))
        assert len(events) == 1  # Allowed

    def test_one_failure_doesnt_block_others(self):
        events = []
        bad = CallbackNotifier(lambda e: 1/0)
        good = CallbackNotifier(events.append)
        composite = CompositeNotifier([bad, good])

        composite.notify(NotificationEvent(type=EventType.COMPLETION, task_name="t"))
        assert len(events) == 1


# ---------------------------------------------------------------------------
# HTTPNotifier
# ---------------------------------------------------------------------------

class TestHTTPNotifier:
    def test_sends_json(self):
        with patch("autocontext.notifications.http.urllib.request.urlopen") as mock_urlopen:
            n = HTTPNotifier("https://example.com/hook")
            e = NotificationEvent(type=EventType.THRESHOLD_MET, task_name="test", score=0.95)
            n.notify(e)

            mock_urlopen.assert_called_once()
            req = mock_urlopen.call_args[0][0]
            assert req.full_url == "https://example.com/hook"
            body = json.loads(req.data)
            assert body["type"] == "threshold_met"
            assert body["score"] == 0.95

    def test_swallows_errors(self):
        with patch("autocontext.notifications.http.urllib.request.urlopen", side_effect=Exception("fail")):
            n = HTTPNotifier("https://example.com/hook")
            e = NotificationEvent(type=EventType.FAILURE, task_name="test")
            n.notify(e)  # Should not raise


# ---------------------------------------------------------------------------
# SlackWebhookNotifier
# ---------------------------------------------------------------------------

class TestSlackWebhookNotifier:
    def test_sends_blocks(self):
        with patch("autocontext.notifications.slack.urllib.request.urlopen") as mock_urlopen:
            n = SlackWebhookNotifier("https://hooks.slack.com/test")
            e = NotificationEvent(type=EventType.THRESHOLD_MET, task_name="rlm-post", score=0.95, round_count=3)
            n.notify(e)

            mock_urlopen.assert_called_once()
            req = mock_urlopen.call_args[0][0]
            body = json.loads(req.data)
            assert "blocks" in body
            # Should have header, summary, and fields sections
            assert len(body["blocks"]) >= 2

    def test_includes_channel(self):
        with patch("autocontext.notifications.slack.urllib.request.urlopen") as mock_urlopen:
            n = SlackWebhookNotifier("https://hooks.slack.com/test", channel="#autocontext-alerts")
            e = NotificationEvent(type=EventType.COMPLETION, task_name="test")
            n.notify(e)

            body = json.loads(mock_urlopen.call_args[0][0].data)
            assert body["channel"] == "#autocontext-alerts"

    def test_swallows_errors(self):
        with patch("autocontext.notifications.slack.urllib.request.urlopen", side_effect=Exception("fail")):
            n = SlackWebhookNotifier("https://hooks.slack.com/test")
            e = NotificationEvent(type=EventType.FAILURE, task_name="test")
            n.notify(e)


# ---------------------------------------------------------------------------
# TaskRunner integration
# ---------------------------------------------------------------------------

class TestTaskRunnerNotifications:
    def test_runner_emits_on_completion(self, tmp_path):
        from pathlib import Path

        from autocontext.execution.task_runner import TaskRunner
        from autocontext.providers.base import CompletionResult, LLMProvider
        from autocontext.storage.sqlite_store import SQLiteStore

        class MockProvider(LLMProvider):
            def __init__(self):
                self._idx = 0
                self._responses = [
                    "Generated output",
                    "<!-- JUDGE_RESULT_START -->\n"
                    '{"score": 0.95, "reasoning": "great", "dimensions": {}}\n'
                    "<!-- JUDGE_RESULT_END -->",
                ]
            def complete(self, system_prompt, user_prompt, model=None, temperature=0.0, max_tokens=4096):
                text = self._responses[self._idx % len(self._responses)]
                self._idx += 1
                return CompletionResult(text=text, model="mock")
            def default_model(self):
                return "mock"

        store = SQLiteStore(tmp_path / "test.db")
        migrations = Path(__file__).parent.parent / "migrations"
        store.migrate(migrations)

        events = []
        notifier = CallbackNotifier(events.append)

        store.enqueue_task("t1", "spec", config={"task_prompt": "write", "rubric": "quality"})
        runner = TaskRunner(store=store, provider=MockProvider(), notifier=notifier)
        runner.run_once()

        assert len(events) == 1
        assert events[0].type == EventType.THRESHOLD_MET
        assert events[0].score == 0.95

    def test_runner_emits_on_failure(self, tmp_path):
        from pathlib import Path

        from autocontext.execution.task_runner import TaskRunner
        from autocontext.providers.base import LLMProvider
        from autocontext.storage.sqlite_store import SQLiteStore

        class FailProvider(LLMProvider):
            def complete(self, *a, **kw):
                raise RuntimeError("API down")
            def default_model(self):
                return "fail"

        store = SQLiteStore(tmp_path / "test.db")
        migrations = Path(__file__).parent.parent / "migrations"
        store.migrate(migrations)

        events = []
        notifier = CallbackNotifier(events.append)

        store.enqueue_task("t1", "spec", config={"task_prompt": "write", "rubric": "quality"})
        runner = TaskRunner(store=store, provider=FailProvider(), notifier=notifier)
        runner.run_once()

        assert len(events) == 1
        assert events[0].type == EventType.FAILURE
        assert "API down" in events[0].error
