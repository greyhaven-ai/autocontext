"""Tests for compact action labels (AC-513).

DDD: ActionLabel is a value object — a short, scannable description
derived from events, tool calls, and step outcomes.
"""

from __future__ import annotations


class TestActionLabel:
    """ActionLabel value object for timeline/event display."""

    def test_create_from_text(self) -> None:
        from autocontext.session.action_labels import ActionLabel

        label = ActionLabel.create("Wrote unit tests for auth module")
        assert label.text == "Wrote unit tests for auth module"
        assert label.category == "action"

    def test_truncates_long_text(self) -> None:
        from autocontext.session.action_labels import ActionLabel

        label = ActionLabel.create("x" * 500)
        assert len(label.text) <= 120
        assert label.text.endswith("…")

    def test_category_tagging(self) -> None:
        from autocontext.session.action_labels import ActionLabel

        assert ActionLabel.create("Ran tests", category="tool").category == "tool"
        assert ActionLabel.create("Error: timeout", category="failure").category == "failure"

    def test_noop_label(self) -> None:
        from autocontext.session.action_labels import ActionLabel

        label = ActionLabel.noop("No changes needed")
        assert label.category == "noop"


class TestLabelFromEvent:
    """Labels derived from coordinator/session events."""

    def test_from_coordinator_event(self) -> None:
        from autocontext.session.action_labels import label_from_event
        from autocontext.session.coordinator import CoordinatorEvent, CoordinatorEventType

        event = CoordinatorEvent(
            event_type=CoordinatorEventType.WORKER_COMPLETED,
            payload={"worker_id": "w1", "coordinator_id": "c1"},
        )
        label = label_from_event(event)
        assert "completed" in label.text.lower()
        assert label.category == "action"

    def test_from_session_event(self) -> None:
        from autocontext.session.action_labels import label_from_event
        from autocontext.session.types import SessionEvent, SessionEventType

        event = SessionEvent(
            event_type=SessionEventType.TURN_COMPLETED,
            payload={"session_id": "s1", "turn_id": "t1", "tokens_used": 150},
        )
        label = label_from_event(event)
        assert label.text
        assert label.category == "action"

    def test_failure_event_gets_failure_category(self) -> None:
        from autocontext.session.action_labels import label_from_event
        from autocontext.session.coordinator import CoordinatorEvent, CoordinatorEventType

        event = CoordinatorEvent(
            event_type=CoordinatorEventType.WORKER_FAILED,
            payload={"worker_id": "w1", "error": "timeout"},
        )
        label = label_from_event(event)
        assert label.category == "failure"


class TestLabelBatch:
    """Batch labeling for timeline display."""

    def test_label_batch_from_coordinator(self) -> None:
        from autocontext.session.action_labels import labels_from_coordinator
        from autocontext.session.coordinator import Coordinator

        coord = Coordinator.create(session_id="s1", goal="test")
        w = coord.delegate(task="Research auth", role="researcher")
        w.start()
        coord.complete_worker(w.worker_id, result="done")

        labels = labels_from_coordinator(coord, max_labels=10)
        assert len(labels) == len(coord.events) == 3
        assert labels[0].text == "Coordinator started"
        assert labels[1].text.startswith("Worker delegated:")
        assert "task=Research auth" in labels[1].text
        assert "role=researcher" in labels[1].text
        assert "worker_id=" in labels[1].text
        assert labels[2].text.startswith("Worker completed:")
        assert "worker_id=" in labels[2].text

    def test_max_labels_respected(self) -> None:
        from autocontext.session.action_labels import labels_from_coordinator
        from autocontext.session.coordinator import Coordinator

        coord = Coordinator.create(session_id="s1", goal="test")
        for i in range(20):
            coord.delegate(task=f"task-{i}", role="r1")

        labels = labels_from_coordinator(coord, max_labels=5)
        assert len(labels) == 5
        assert all(label.text.startswith("Worker delegated") for label in labels)
