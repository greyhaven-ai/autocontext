"""Tests for AC-769 failure-type → remediation routing.

Pattern-match a ``FailureReport`` (and optionally a fixtures map from AC-767
or an imports map for AC-768) to typed remediation hints. Each rule is a
pure function: pluggable, independently testable.

Acceptance criteria from the issue:
  - Off-by-one heuristic produces ``SmallCaseVerify``.
  - ``TypeError: positional`` produces ``SurfaceSignatures``.
  - missing-substring + stale fixture produces ``RefreshFixture``.
  - Empty FailureReport produces no hints.
  - Integration: refinement prompt includes "Suggested next moves".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from autocontext.harness.evaluation.failure_report import FailureReport, MatchDiagnosis
from autocontext.loop.fixture_loader import Fixture, FixtureProvenance
from autocontext.loop.remediation_router import (
    DEFAULT_RULES,
    RefreshFixture,
    RemediationHint,
    SmallCaseVerify,
    SurfaceSignatures,
    render_hints,
    route_remediations,
    rule_off_by_one,
    rule_positional_typerror,
    rule_stale_fixture,
)


def _report(*errors_per_match: list[str]) -> FailureReport:
    """Build a FailureReport with one MatchDiagnosis per provided error list."""
    diagnoses = [
        MatchDiagnosis(
            match_index=i,
            score=0.0,
            passed=False,
            errors=list(errs),
            summary=f"Match {i}",
        )
        for i, errs in enumerate(errors_per_match)
    ]
    return FailureReport(
        match_diagnoses=diagnoses,
        overall_delta=-0.01,
        threshold=0.0,
        previous_best=1.0,
        current_best=0.99,
        strategy_summary="{}",
    )


def _fixture(key: str, *, age_days: int = 0) -> Fixture:
    fetched_at = (datetime.now(tz=UTC) - timedelta(days=age_days)).isoformat(timespec="seconds")
    prov = FixtureProvenance(source="https://example.com", fetched_at=fetched_at, sha256="x" * 64)
    return Fixture(key=key, bytes_=b"...", provenance=prov)


# --- TestRuleOffByOne -----------------------------------------------------


class TestRuleOffByOne:
    def test_expected_vs_got_one_apart(self) -> None:
        report = _report(["AssertionError: expected 138, got 139"])
        hints = rule_off_by_one(report)
        assert len(hints) == 1
        assert isinstance(hints[0], SmallCaseVerify)

    def test_off_by_sixteen_block_size(self) -> None:
        report = _report(["expected 256 bytes, got 272"])
        hints = rule_off_by_one(report)
        assert any(isinstance(h, SmallCaseVerify) for h in hints)

    def test_no_numeric_diff_no_hint(self) -> None:
        report = _report(["random error message"])
        assert rule_off_by_one(report) == []

    def test_diff_far_from_block_multiple_no_hint(self) -> None:
        # Diff of 1000 isn't an off-by-one or block-multiple; skip.
        report = _report(["expected 100, got 1100"])
        assert rule_off_by_one(report) == []


# --- TestRulePositionalTypeError ------------------------------------------


class TestRulePositionalTypeError:
    def test_positional_typerror_produces_surface_signatures(self) -> None:
        err = (
            'File "/path/c35.py", line 7, in main\n'
            "    pt = cbc_decrypt(key, ct, iv)\n"
            'File "/path/c10_cbc_mode.py", line 14, in cbc_decrypt\n'
            "TypeError: cbc_decrypt() takes 3 positional arguments but 4 were given"
        )
        report = _report([err])
        hints = rule_positional_typerror(report)
        assert len(hints) == 1
        hint = hints[0]
        assert isinstance(hint, SurfaceSignatures)
        # Module names should come from the traceback paths.
        assert "c10_cbc_mode" in hint.modules or "c35" in hint.modules

    def test_non_positional_typerror_skipped(self) -> None:
        report = _report(["TypeError: unsupported operand type(s) for +: 'int' and 'str'"])
        assert rule_positional_typerror(report) == []

    def test_no_typerror_no_hint(self) -> None:
        report = _report(["something else broke"])
        assert rule_positional_typerror(report) == []


# --- TestRuleStaleFixture -------------------------------------------------


class TestRuleStaleFixture:
    def test_missing_substring_with_stale_fixture_produces_refresh(self) -> None:
        report = _report(
            [
                "contract-probe failure: missing-substring 'cake' in artifact challenge_19_data",
            ]
        )
        fixtures = {"challenge_19_data": _fixture("challenge_19_data", age_days=14)}
        hints = rule_stale_fixture(report, fixtures=fixtures, stale_after_days=7)
        assert len(hints) == 1
        assert isinstance(hints[0], RefreshFixture)
        assert hints[0].key == "challenge_19_data"

    def test_missing_substring_with_fresh_fixture_no_hint(self) -> None:
        report = _report(["missing-substring 'cake' in artifact data_c19"])
        fixtures = {"data_c19": _fixture("data_c19", age_days=1)}
        assert rule_stale_fixture(report, fixtures=fixtures, stale_after_days=7) == []

    def test_no_fixture_match_no_hint(self) -> None:
        # Error doesn't reference a known fixture key.
        report = _report(["missing-substring 'unknown_key' in something"])
        fixtures = {"other_key": _fixture("other_key", age_days=99)}
        assert rule_stale_fixture(report, fixtures=fixtures, stale_after_days=7) == []

    def test_no_fixtures_arg_no_hint(self) -> None:
        report = _report(["missing-substring 'x' in artifact y"])
        assert rule_stale_fixture(report, fixtures=None) == []


# --- TestRouteRemediations ------------------------------------------------


class TestRouteRemediations:
    def test_empty_report_no_hints(self) -> None:
        report = _report()  # zero diagnoses
        assert route_remediations(report) == []

    def test_diagnoses_with_no_errors_no_hints(self) -> None:
        report = _report([])  # one diagnosis, no errors
        assert route_remediations(report) == []

    def test_multiple_rules_can_fire(self) -> None:
        report = _report(
            [
                "expected 138, got 139",
                ('File "/path/foo_caller.py", line 3, in main\nTypeError: foo() takes 2 positional arguments but 3 were given'),
            ]
        )
        hints = route_remediations(report)
        kinds = {type(h) for h in hints}
        assert SmallCaseVerify in kinds
        assert SurfaceSignatures in kinds

    def test_custom_rules_pluggable(self) -> None:
        def always_fire(report: FailureReport, **_: object) -> list[RemediationHint]:
            return [SmallCaseVerify(function=None, reason="always")]

        hints = route_remediations(_report(["anything"]), rules=[always_fire])
        assert len(hints) == 1
        assert hints[0].reason == "always"

    def test_default_rules_set_documented(self) -> None:
        # The default rules list is exported so callers can extend rather than replace.
        assert rule_off_by_one in DEFAULT_RULES
        assert rule_positional_typerror in DEFAULT_RULES
        assert rule_stale_fixture in DEFAULT_RULES


# --- TestRender -----------------------------------------------------------


class TestRender:
    def test_empty_renders_empty(self) -> None:
        assert render_hints([]) == ""

    def test_includes_section_header(self) -> None:
        hints = [SmallCaseVerify(function="detect_secret_len", reason="off-by-one in match 0")]
        out = render_hints(hints)
        assert "## Suggested next moves" in out
        assert "small-case verify" in out.lower() or "smallcaseverify" in out.lower()
        assert "detect_secret_len" in out
        assert "off-by-one" in out

    def test_refinement_prompt_includes_hints_block(self) -> None:
        """Integration: AC-769 hints flow through build_refinement_prompt."""
        from autocontext.loop.refinement_prompt import build_refinement_prompt

        report = _report(["expected 138, got 139"])
        hints = route_remediations(report)
        block = render_hints(hints)

        prompt = build_refinement_prompt(
            scenario_rules="rules",
            strategy_interface="iface",
            evaluation_criteria="crit",
            parent_strategy="x = 1",
            match_feedback="off-by-one in match 0",
            remediation_hints=block,
        )
        assert "## Suggested next moves" in prompt
        assert "small-case verify" in prompt.lower()

    def test_refinement_prompt_no_hints_omits_block(self) -> None:
        from autocontext.loop.refinement_prompt import build_refinement_prompt

        prompt = build_refinement_prompt(
            scenario_rules="r",
            strategy_interface="i",
            evaluation_criteria="c",
            parent_strategy="x",
            match_feedback="m",
        )
        assert "Suggested next moves" not in prompt

    def test_renders_all_hint_kinds(self) -> None:
        hints: list[RemediationHint] = [
            RefreshFixture(key="data_c19", reason="stale 14 days"),
            SurfaceSignatures(modules=("c10_cbc_mode",), reason="positional TypeError"),
            SmallCaseVerify(function="detect_secret_len", reason="off-by-one"),
        ]
        out = render_hints(hints)
        assert "data_c19" in out
        assert "c10_cbc_mode" in out
        assert "detect_secret_len" in out


class TestStageTreeSearchWiring:
    """Reviewer P2 (PR #971): the production refinement loop must call
    ``route_remediations`` from the most recent tournament's errors and
    thread the rendered hints into ``build_refinement_prompt``.

    We test the wiring at the seam exposed for this purpose:
    ``stage_tree_search.remediation_hints_for_node`` takes a HypothesisNode
    plus the GenerationContext-shaped fixtures dict and returns a rendered
    prompt block."""

    def test_node_with_off_by_one_errors_produces_smallcaseverify_block(self) -> None:
        from autocontext.loop.hypothesis_tree import HypothesisNode
        from autocontext.loop.stage_tree_search import remediation_hints_for_node

        node = HypothesisNode(
            id="n1",
            strategy={"__code__": "x = 1"},
            parent_id=None,
            scores=[0.3, 0.4],
            elo=950.0,
            generation=1,
            refinement_count=0,
            last_errors=[["AssertionError: expected 138, got 139"]],
        )
        block = remediation_hints_for_node(node, fixtures={})
        assert "## Suggested next moves" in block
        assert "small-case verify" in block.lower()

    def test_node_without_errors_returns_empty_block(self) -> None:
        from autocontext.loop.hypothesis_tree import HypothesisNode
        from autocontext.loop.stage_tree_search import remediation_hints_for_node

        node = HypothesisNode(
            id="n1",
            strategy={"x": 1},
            parent_id=None,
            scores=[0.9],
            elo=1500.0,
            generation=1,
            refinement_count=0,
            last_errors=[],
        )
        assert remediation_hints_for_node(node, fixtures={}) == ""
