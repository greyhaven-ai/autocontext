"""Tests for AC-336 + AC-335: analyst quality scoring and tool usage tracking.

AC-336: AnalystRating, format_analyst_feedback
AC-335: ToolUsageRecord, ToolUsageTracker, format_utilization_report, identify_stale_tools
"""

from __future__ import annotations

# ===========================================================================
# AC-336: AnalystRating
# ===========================================================================


class TestAnalystRating:
    def test_construction(self) -> None:
        from autocontext.agents.feedback_loops import AnalystRating

        rating = AnalystRating(
            actionability=4,
            specificity=3,
            correctness=5,
            rationale="Findings were specific but could be more actionable.",
            generation=4,
        )
        assert rating.actionability == 4
        assert rating.overall == 4.0  # mean of 4, 3, 5

    def test_overall_score(self) -> None:
        from autocontext.agents.feedback_loops import AnalystRating

        rating = AnalystRating(actionability=1, specificity=1, correctness=1, rationale="", generation=1)
        assert rating.overall == 1.0

        rating2 = AnalystRating(actionability=5, specificity=5, correctness=5, rationale="", generation=1)
        assert rating2.overall == 5.0

    def test_roundtrip(self) -> None:
        from autocontext.agents.feedback_loops import AnalystRating

        rating = AnalystRating(actionability=3, specificity=4, correctness=2, rationale="test", generation=3)
        d = rating.to_dict()
        assert d["overall"] == 3.0
        restored = AnalystRating.from_dict(d)
        assert restored.specificity == 4
        assert restored.generation == 3


# ===========================================================================
# AC-336: format_analyst_feedback
# ===========================================================================


class TestFormatAnalystFeedback:
    def test_formats_rating(self) -> None:
        from autocontext.agents.feedback_loops import AnalystRating, format_analyst_feedback

        rating = AnalystRating(
            actionability=2, specificity=2, correctness=4,
            rationale="Findings were too vague to act on.",
            generation=4,
        )
        text = format_analyst_feedback(rating)
        assert "generation 4" in text.lower() or "gen 4" in text.lower()
        assert "2" in text
        assert "vague" in text.lower()

    def test_high_rating_still_formats(self) -> None:
        from autocontext.agents.feedback_loops import AnalystRating, format_analyst_feedback

        rating = AnalystRating(
            actionability=5, specificity=5, correctness=5,
            rationale="Excellent analysis with concrete evidence.",
            generation=7,
        )
        text = format_analyst_feedback(rating)
        assert len(text) > 0

    def test_none_rating_returns_empty(self) -> None:
        from autocontext.agents.feedback_loops import format_analyst_feedback

        text = format_analyst_feedback(None)
        assert text == ""


# ===========================================================================
# AC-335: ToolUsageRecord
# ===========================================================================


class TestToolUsageRecord:
    def test_construction(self) -> None:
        from autocontext.agents.feedback_loops import ToolUsageRecord

        rec = ToolUsageRecord(
            tool_name="cluster_evaluator",
            used_in_gens=[3, 5, 7],
            last_used=7,
            total_refs=3,
        )
        assert rec.tool_name == "cluster_evaluator"
        assert rec.total_refs == 3

    def test_roundtrip(self) -> None:
        from autocontext.agents.feedback_loops import ToolUsageRecord

        rec = ToolUsageRecord(tool_name="test", used_in_gens=[1], last_used=1, total_refs=1)
        d = rec.to_dict()
        restored = ToolUsageRecord.from_dict(d)
        assert restored.tool_name == "test"


# ===========================================================================
# AC-335: ToolUsageTracker
# ===========================================================================


class TestToolUsageTracker:
    def test_scan_strategy_text(self) -> None:
        from autocontext.agents.feedback_loops import ToolUsageTracker

        tracker = ToolUsageTracker(known_tools=["cluster_evaluator", "move_predictor", "path_optimizer"])
        tracker.record_generation(
            generation=3,
            strategy_text='Using cluster_evaluator to analyze positions and path_optimizer for routing.',
        )

        stats = tracker.get_stats()
        assert stats["cluster_evaluator"].total_refs == 1
        assert stats["path_optimizer"].total_refs == 1
        assert stats["move_predictor"].total_refs == 0

    def test_multiple_generations(self) -> None:
        from autocontext.agents.feedback_loops import ToolUsageTracker

        tracker = ToolUsageTracker(known_tools=["tool_a", "tool_b"])
        tracker.record_generation(3, "Using tool_a here")
        tracker.record_generation(4, "Using tool_a and tool_b")
        tracker.record_generation(5, "Using tool_a again")

        stats = tracker.get_stats()
        assert stats["tool_a"].total_refs == 3
        assert stats["tool_a"].last_used == 5
        assert stats["tool_b"].total_refs == 1

    def test_empty_strategy(self) -> None:
        from autocontext.agents.feedback_loops import ToolUsageTracker

        tracker = ToolUsageTracker(known_tools=["tool_a"])
        tracker.record_generation(1, "")
        assert tracker.get_stats()["tool_a"].total_refs == 0


# ===========================================================================
# AC-335: format_utilization_report
# ===========================================================================


class TestFormatUtilizationReport:
    def test_formats_report(self) -> None:
        from autocontext.agents.feedback_loops import ToolUsageTracker, format_utilization_report

        tracker = ToolUsageTracker(known_tools=["tool_a", "tool_b", "tool_c"])
        tracker.record_generation(1, "tool_a used")
        tracker.record_generation(2, "tool_a used again")
        tracker.record_generation(3, "tool_a and tool_b used")

        report = format_utilization_report(tracker, current_generation=3, window=3)
        assert "tool_a" in report
        assert "tool_c" in report  # unused tool mentioned
        assert "HIGH" in report or "UNUSED" in report

    def test_empty_tracker(self) -> None:
        from autocontext.agents.feedback_loops import ToolUsageTracker, format_utilization_report

        tracker = ToolUsageTracker(known_tools=[])
        report = format_utilization_report(tracker, current_generation=5, window=5)
        assert report == "" or "no tools" in report.lower()

    def test_report_ages_out_old_uses(self) -> None:
        from autocontext.agents.feedback_loops import ToolUsageTracker, format_utilization_report

        tracker = ToolUsageTracker(known_tools=["tool_a"])
        tracker.record_generation(1, "tool_a used")

        report = format_utilization_report(tracker, current_generation=10, window=3)
        assert "used 0/3 gens" in report
        assert "UNUSED" in report


# ===========================================================================
# AC-335: identify_stale_tools
# ===========================================================================


class TestIdentifyStaleTools:
    def test_finds_stale(self) -> None:
        from autocontext.agents.feedback_loops import ToolUsageTracker, identify_stale_tools

        tracker = ToolUsageTracker(known_tools=["active", "stale"])
        tracker.record_generation(1, "active used")
        tracker.record_generation(2, "active used")
        tracker.record_generation(3, "active used")
        tracker.record_generation(4, "active used")
        tracker.record_generation(5, "active used")

        stale = identify_stale_tools(tracker, current_generation=5, archive_after_gens=3)
        assert "stale" in stale

    def test_no_stale_when_recently_used(self) -> None:
        from autocontext.agents.feedback_loops import ToolUsageTracker, identify_stale_tools

        tracker = ToolUsageTracker(known_tools=["tool_a"])
        tracker.record_generation(5, "tool_a used")

        stale = identify_stale_tools(tracker, current_generation=5, archive_after_gens=3)
        assert "tool_a" not in stale

    def test_never_used_is_stale(self) -> None:
        from autocontext.agents.feedback_loops import ToolUsageTracker, identify_stale_tools

        tracker = ToolUsageTracker(known_tools=["unused_tool"])
        # Record 5 generations without using the tool
        for g in range(1, 6):
            tracker.record_generation(g, "no tools here")

        stale = identify_stale_tools(tracker, current_generation=5, archive_after_gens=3)
        assert "unused_tool" in stale
