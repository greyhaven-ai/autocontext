"""Tests for HarnessTester — parallel harness validation against sample states."""
from __future__ import annotations

import textwrap
import time
from unittest.mock import patch

import pytest

from autocontext.execution.harness_tester import HarnessTester, HarnessTestFailure, HarnessTestReport
from autocontext.execution.sample_states import SampleState

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_state(turn: int = 0, difficulty: str = "early") -> SampleState:
    return SampleState(
        state={"turn": turn, "terminal": False},
        description=f"Turn {turn}",
        expected_legal_actions=[{"action": "up"}, {"action": "down"}],
        difficulty=difficulty,
    )


def _make_states(n: int = 10) -> list[SampleState]:
    phases = ["early", "mid", "late"]
    return [_make_state(turn=i, difficulty=phases[i % 3]) for i in range(n)]


GOOD_HARNESS = textwrap.dedent("""\
    def validate_strategy(strategy, scenario):
        return True, []

    def enumerate_legal_actions(state):
        return [{"action": "up"}, {"action": "down"}]

    def is_legal_action(state, action):
        return action.get("action") in ("up", "down")
""")

BAD_HARNESS_WRONG_RESULT = textwrap.dedent("""\
    def validate_strategy(strategy, scenario):
        return True, []

    def enumerate_legal_actions(state):
        return [{"action": "left"}]

    def is_legal_action(state, action):
        return action.get("action") == "left"
""")

# Use division by zero — works within restricted builtins (no named exception needed)
BAD_HARNESS_RAISES = textwrap.dedent("""\
    def validate_strategy(strategy, scenario):
        return True, []

    def enumerate_legal_actions(state):
        return 1 / 0

    def is_legal_action(state, action):
        return 1 / 0
""")

BAD_HARNESS_SYNTAX = "def validate_strategy(:\n"

BAD_HARNESS_IMPORT = textwrap.dedent("""\
    import os

    def validate_strategy(strategy, scenario):
        return True, []
""")

BAD_HARNESS_WRONG_METADATA = textwrap.dedent("""\
    def validate_strategy(strategy, scenario):
        return True, []

    def enumerate_legal_actions(state):
        return [
            {"action": "up", "range": [9.0, 9.0]},
            {"action": "down", "range": [9.0, 9.0]},
        ]

    def is_legal_action(state, action):
        return action.get("action") in ("up", "down")
""")


# ── HarnessTestFailure dataclass ──────────────────────────────────────────────


class TestHarnessTestFailure:
    def test_fields(self) -> None:
        f = HarnessTestFailure(
            state={"turn": 1},
            function_name="enumerate_legal_actions",
            expected=[{"action": "up"}],
            actual=[{"action": "left"}],
            error="mismatch",
            state_description="Turn 1",
        )
        assert f.function_name == "enumerate_legal_actions"
        assert f.error == "mismatch"

    def test_frozen(self) -> None:
        f = HarnessTestFailure(
            state={}, function_name="x", expected=None, actual=None, error="e", state_description="d"
        )
        with pytest.raises(AttributeError):
            f.error = "new"  # type: ignore[misc]


# ── HarnessTestReport dataclass ──────────────────────────────────────────────


class TestHarnessTestReport:
    def test_fields(self) -> None:
        r = HarnessTestReport(
            total_tests=10, passed=8, failed=2, accuracy=0.8,
            failures=[], execution_time_ms=123.4,
        )
        assert r.accuracy == 0.8
        assert r.total_tests == 10

    def test_frozen(self) -> None:
        r = HarnessTestReport(
            total_tests=1, passed=1, failed=0, accuracy=1.0,
            failures=[], execution_time_ms=0.0,
        )
        with pytest.raises(AttributeError):
            r.accuracy = 0.5  # type: ignore[misc]


# ── HarnessTester with good harness ──────────────────────────────────────────


class TestHarnessTesterGoodHarness:
    def test_all_pass(self) -> None:
        tester = HarnessTester()
        states = _make_states(10)
        report = tester.test_harness(GOOD_HARNESS, states)
        assert report.passed == report.total_tests
        assert report.failed == 0
        assert report.accuracy == 1.0
        assert report.failures == []

    def test_execution_time_recorded(self) -> None:
        tester = HarnessTester()
        states = _make_states(5)
        report = tester.test_harness(GOOD_HARNESS, states)
        assert report.execution_time_ms >= 0.0

    def test_total_tests_matches_states(self) -> None:
        tester = HarnessTester()
        states = _make_states(7)
        report = tester.test_harness(GOOD_HARNESS, states)
        assert report.total_tests == 7


# ── HarnessTester with bad harness ───────────────────────────────────────────


class TestHarnessTesterBadHarness:
    def test_wrong_result_detected(self) -> None:
        tester = HarnessTester()
        states = _make_states(5)
        report = tester.test_harness(BAD_HARNESS_WRONG_RESULT, states)
        assert report.failed > 0
        assert report.accuracy < 1.0
        assert len(report.failures) > 0

    def test_exception_detected(self) -> None:
        tester = HarnessTester()
        states = _make_states(5)
        report = tester.test_harness(BAD_HARNESS_RAISES, states)
        assert report.failed > 0
        assert any("division by zero" in f.error for f in report.failures)

    def test_syntax_error_detected(self) -> None:
        tester = HarnessTester()
        states = _make_states(3)
        report = tester.test_harness(BAD_HARNESS_SYNTAX, states)
        assert report.failed == report.total_tests
        assert report.accuracy == 0.0

    def test_import_rejected_by_ast_safety(self) -> None:
        tester = HarnessTester()
        states = _make_states(3)
        report = tester.test_harness(BAD_HARNESS_IMPORT, states)
        assert report.failed == report.total_tests
        assert report.accuracy == 0.0

    def test_action_metadata_mismatch_detected(self) -> None:
        tester = HarnessTester()
        states = _make_states(3)
        report = tester.test_harness(BAD_HARNESS_WRONG_METADATA, states)
        assert report.failed > 0
        assert report.accuracy < 1.0


# ── Max failures limit ────────────────────────────────────────────────────────


class TestHarnessTesterMaxFailures:
    def test_default_max_5_failures(self) -> None:
        tester = HarnessTester()
        states = _make_states(20)
        report = tester.test_harness(BAD_HARNESS_RAISES, states)
        assert len(report.failures) <= 5

    def test_custom_max_failures(self) -> None:
        tester = HarnessTester(max_failures_reported=3)
        states = _make_states(20)
        report = tester.test_harness(BAD_HARNESS_RAISES, states)
        assert len(report.failures) <= 3

    def test_diverse_failure_sampling(self) -> None:
        """Failures should be sampled from different phases when possible."""
        tester = HarnessTester(max_failures_reported=5)
        states = _make_states(30)
        report = tester.test_harness(BAD_HARNESS_RAISES, states)
        if len(report.failures) >= 3:
            phases = {f.state_description for f in report.failures}
            # Should have diversity — at least 2 distinct descriptions
            assert len(phases) >= 2


# ── Configurable parallelism ─────────────────────────────────────────────────


class TestHarnessTesterParallelism:
    def test_single_worker(self) -> None:
        tester = HarnessTester(parallel_workers=1)
        states = _make_states(5)
        report = tester.test_harness(GOOD_HARNESS, states)
        assert report.passed == report.total_tests

    def test_many_workers(self) -> None:
        tester = HarnessTester(parallel_workers=20)
        states = _make_states(5)
        report = tester.test_harness(GOOD_HARNESS, states)
        assert report.passed == report.total_tests


# ── Timeout per test ──────────────────────────────────────────────────────────


class TestHarnessTesterTimeout:
    def test_slow_harness_times_out(self) -> None:
        """Verify that the timeout mechanism produces failure reports.

        We use time.sleep in a wrapper (not in the sandbox) to simulate
        a slow harness call without leaving unkillable threads.
        """
        import autocontext.execution.harness_tester as ht_mod

        original_fn = ht_mod._test_single_state

        def _slow_test_single_state(*args: object, **kwargs: object) -> HarnessTestFailure | None:
            time.sleep(5.0)  # Will exceed timeout
            return original_fn(*args, **kwargs)  # type: ignore[arg-type]

        tester = HarnessTester(timeout_per_test=0.2)
        states = _make_states(1)

        with patch.object(ht_mod, "_test_single_state", side_effect=_slow_test_single_state):
            report = tester.test_harness(GOOD_HARNESS, states)

        assert report.failed > 0
        assert any("timed out" in f.error.lower() for f in report.failures)


# ── Empty states ──────────────────────────────────────────────────────────────


class TestHarnessTesterEdgeCases:
    def test_empty_states(self) -> None:
        tester = HarnessTester()
        report = tester.test_harness(GOOD_HARNESS, [])
        assert report.total_tests == 0
        assert report.accuracy == 1.0  # vacuously true

    def test_states_without_ground_truth(self) -> None:
        tester = HarnessTester()
        states = [
            SampleState(state={"turn": 0}, description="no gt", expected_legal_actions=None, difficulty="early"),
        ]
        report = tester.test_harness(GOOD_HARNESS, states)
        # Without ground truth, we can only test that the functions don't crash
        assert report.total_tests == 1
        assert report.passed == 1

    def test_harness_missing_function(self) -> None:
        """Missing required functions should report failures when requested."""
        harness = textwrap.dedent("""\
            def validate_strategy(strategy, scenario):
                return True, []
        """)
        tester = HarnessTester()
        states = _make_states(3)
        report = tester.test_harness(
            harness,
            states,
            required_functions=["validate_strategy", "enumerate_legal_actions", "is_legal_action"],
        )
        assert report.failed == report.total_tests
        assert report.accuracy == 0.0

    def test_validate_strategy_receives_real_contract(self) -> None:
        harness = textwrap.dedent("""\
            def validate_strategy(strategy, scenario):
                return strategy.get("action") == "up" and scenario is not None, []

            def enumerate_legal_actions(state):
                return [{"action": "up"}, {"action": "down"}]

            def is_legal_action(state, action):
                return action.get("action") in ("up", "down")
        """)
        tester = HarnessTester()
        states = _make_states(1)
        report = tester.test_harness(harness, states, scenario=object())
        assert report.failed == 0
