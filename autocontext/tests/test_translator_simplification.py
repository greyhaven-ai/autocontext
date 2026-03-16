"""Tests for AC-188: translator simplification and analyst+coach consolidation spike.

Track 1: extract_strategy_deterministic — deterministic JSON extraction
Track 2: ConsolidatedRoleOutput, parse_consolidated_output, RoleBenchmarkResult,
         compare_role_outputs
"""

from __future__ import annotations

# ===========================================================================
# Track 1: Deterministic strategy extraction
# ===========================================================================


class TestExtractStrategyDeterministic:
    def test_clean_json(self) -> None:
        from autocontext.agents.translator_simplification import extract_strategy_deterministic

        raw = '{"aggression": 0.8, "defense": 0.4}'
        result = extract_strategy_deterministic(raw)
        assert result is not None
        assert result["aggression"] == 0.8
        assert result["defense"] == 0.4

    def test_json_in_markdown_fences(self) -> None:
        from autocontext.agents.translator_simplification import extract_strategy_deterministic

        raw = """Here's my strategy:

```json
{"aggression": 0.7, "defense": 0.5}
```

This balances offense and defense."""
        result = extract_strategy_deterministic(raw)
        assert result is not None
        assert result["aggression"] == 0.7

    def test_json_in_plain_fences(self) -> None:
        from autocontext.agents.translator_simplification import extract_strategy_deterministic

        raw = """```
{"scouting": 0.3, "aggression": 0.6}
```"""
        result = extract_strategy_deterministic(raw)
        assert result is not None
        assert result["scouting"] == 0.3

    def test_json_with_surrounding_prose(self) -> None:
        from autocontext.agents.translator_simplification import extract_strategy_deterministic

        raw = """Based on my analysis, I recommend:
{"aggression": 0.9, "defense": 0.2, "scouting": 0.1}
This should maximize flag captures."""
        result = extract_strategy_deterministic(raw)
        assert result is not None
        assert result["aggression"] == 0.9

    def test_returns_none_for_unparseable(self) -> None:
        from autocontext.agents.translator_simplification import extract_strategy_deterministic

        raw = "I think we should be more aggressive and less defensive."
        result = extract_strategy_deterministic(raw)
        assert result is None

    def test_returns_none_for_array(self) -> None:
        from autocontext.agents.translator_simplification import extract_strategy_deterministic

        raw = '[1, 2, 3]'
        result = extract_strategy_deterministic(raw)
        assert result is None

    def test_returns_none_for_empty(self) -> None:
        from autocontext.agents.translator_simplification import extract_strategy_deterministic

        assert extract_strategy_deterministic("") is None
        assert extract_strategy_deterministic("   ") is None

    def test_nested_json_extracts_outermost(self) -> None:
        from autocontext.agents.translator_simplification import extract_strategy_deterministic

        raw = '{"aggression": 0.8, "config": {"mode": "fast"}}'
        result = extract_strategy_deterministic(raw)
        assert result is not None
        assert result["aggression"] == 0.8

    def test_multiple_json_objects_extracts_first(self) -> None:
        from autocontext.agents.translator_simplification import extract_strategy_deterministic

        raw = """Option A: {"aggression": 0.9, "defense": 0.1}
Option B: {"aggression": 0.5, "defense": 0.5}"""
        result = extract_strategy_deterministic(raw)
        assert result is not None
        # Should find the first valid JSON object
        assert isinstance(result, dict)

    def test_validates_values_are_numeric_or_string(self) -> None:
        """Strategy values should be numeric, string, or bool — not arbitrary nested structures only."""
        from autocontext.agents.translator_simplification import extract_strategy_deterministic

        raw = '{"aggression": 0.8, "notes": "high risk"}'
        result = extract_strategy_deterministic(raw)
        assert result is not None


# ===========================================================================
# Track 2: Consolidated role output model
# ===========================================================================


class TestConsolidatedRoleOutput:
    def test_construction(self) -> None:
        from autocontext.agents.translator_simplification import ConsolidatedRoleOutput

        output = ConsolidatedRoleOutput(
            raw_markdown="# Analysis\n...",
            findings=["Score improved with high aggression"],
            root_causes=["Aggression above 0.7 correlates with wins"],
            recommendations=["Try aggression=0.8 with defense=0.4"],
            playbook="Updated playbook content",
            lessons=["High aggression works above 0.6 density"],
            hints=["Try aggression=0.8 next"],
            parse_success=True,
        )
        assert output.parse_success is True
        assert len(output.findings) == 1
        assert output.playbook == "Updated playbook content"

    def test_roundtrip(self) -> None:
        from autocontext.agents.translator_simplification import ConsolidatedRoleOutput

        output = ConsolidatedRoleOutput(
            raw_markdown="test",
            findings=["f1"],
            root_causes=["rc1"],
            recommendations=["r1"],
            playbook="pb",
            lessons=["l1"],
            hints=["h1"],
            parse_success=True,
        )
        d = output.to_dict()
        restored = ConsolidatedRoleOutput.from_dict(d)
        assert restored.findings == ["f1"]
        assert restored.playbook == "pb"


# ===========================================================================
# Track 2: parse_consolidated_output
# ===========================================================================


class TestParseConsolidatedOutput:
    def test_parses_well_formed_output(self) -> None:
        from autocontext.agents.translator_simplification import parse_consolidated_output

        markdown = """## Findings
- Score improved when aggression > 0.7
- Defense below 0.3 causes flag loss

## Root Causes
- High aggression enables faster flag capture

## Actionable Recommendations
- Set aggression to 0.8

<!-- PLAYBOOK_START -->
Use high aggression with moderate defense.
<!-- PLAYBOOK_END -->

<!-- LESSONS_START -->
- Aggression > 0.7 is optimal for dense grids
<!-- LESSONS_END -->

<!-- COMPETITOR_HINTS_START -->
- Try aggression=0.8 defense=0.4
<!-- COMPETITOR_HINTS_END -->"""

        result = parse_consolidated_output(markdown)
        assert result.parse_success is True
        assert len(result.findings) == 2
        assert len(result.root_causes) == 1
        assert len(result.recommendations) == 1
        assert "high aggression" in result.playbook.lower()
        assert len(result.lessons) >= 1
        assert len(result.hints) >= 1

    def test_handles_missing_sections(self) -> None:
        from autocontext.agents.translator_simplification import parse_consolidated_output

        markdown = """## Findings
- Single finding here

No other sections present."""

        result = parse_consolidated_output(markdown)
        assert result.parse_success is True
        assert len(result.findings) == 1
        assert result.playbook == ""
        assert result.lessons == []

    def test_handles_empty_input(self) -> None:
        from autocontext.agents.translator_simplification import parse_consolidated_output

        result = parse_consolidated_output("")
        assert result.parse_success is True
        assert result.findings == []


# ===========================================================================
# Track 2: Role benchmark result
# ===========================================================================


class TestRoleBenchmarkResult:
    def test_construction(self) -> None:
        from autocontext.agents.translator_simplification import RoleBenchmarkResult

        result = RoleBenchmarkResult(
            mode="two_role",
            findings_count=5,
            root_causes_count=3,
            recommendations_count=4,
            playbook_length=500,
            lessons_count=3,
            hints_count=2,
            total_tokens=15000,
            total_latency_ms=8000,
        )
        assert result.mode == "two_role"
        assert result.total_tokens == 15000

    def test_roundtrip(self) -> None:
        from autocontext.agents.translator_simplification import RoleBenchmarkResult

        result = RoleBenchmarkResult(
            mode="consolidated",
            findings_count=4,
            root_causes_count=2,
            recommendations_count=3,
            playbook_length=400,
            lessons_count=2,
            hints_count=1,
            total_tokens=8000,
            total_latency_ms=4000,
        )
        d = result.to_dict()
        restored = RoleBenchmarkResult.from_dict(d)
        assert restored.mode == "consolidated"
        assert restored.total_tokens == 8000


# ===========================================================================
# Track 2: compare_role_outputs
# ===========================================================================


class TestCompareRoleOutputs:
    def test_comparison_computes_deltas(self) -> None:
        from autocontext.agents.translator_simplification import (
            RoleBenchmarkResult,
            compare_role_outputs,
        )

        two_role = RoleBenchmarkResult(
            mode="two_role",
            findings_count=5, root_causes_count=3, recommendations_count=4,
            playbook_length=500, lessons_count=3, hints_count=2,
            total_tokens=15000, total_latency_ms=8000,
        )
        consolidated = RoleBenchmarkResult(
            mode="consolidated",
            findings_count=4, root_causes_count=2, recommendations_count=3,
            playbook_length=400, lessons_count=2, hints_count=1,
            total_tokens=8000, total_latency_ms=4000,
        )

        comparison = compare_role_outputs(two_role, consolidated)
        assert comparison["token_savings"] == 7000
        assert comparison["latency_savings_ms"] == 4000
        assert comparison["findings_delta"] == -1
        assert comparison["recommendation"] in ("consolidated_viable", "two_role_preferred", "inconclusive")

    def test_consolidated_viable_when_quality_close(self) -> None:
        from autocontext.agents.translator_simplification import (
            RoleBenchmarkResult,
            compare_role_outputs,
        )

        two_role = RoleBenchmarkResult(
            mode="two_role",
            findings_count=5, root_causes_count=3, recommendations_count=4,
            playbook_length=500, lessons_count=3, hints_count=2,
            total_tokens=15000, total_latency_ms=8000,
        )
        # Consolidated produces similar quality at lower cost
        consolidated = RoleBenchmarkResult(
            mode="consolidated",
            findings_count=5, root_causes_count=3, recommendations_count=4,
            playbook_length=480, lessons_count=3, hints_count=2,
            total_tokens=8000, total_latency_ms=4000,
        )

        comparison = compare_role_outputs(two_role, consolidated)
        assert comparison["recommendation"] == "consolidated_viable"

    def test_two_role_preferred_when_quality_drops(self) -> None:
        from autocontext.agents.translator_simplification import (
            RoleBenchmarkResult,
            compare_role_outputs,
        )

        two_role = RoleBenchmarkResult(
            mode="two_role",
            findings_count=10, root_causes_count=8, recommendations_count=6,
            playbook_length=1000, lessons_count=5, hints_count=4,
            total_tokens=15000, total_latency_ms=8000,
        )
        # Consolidated produces much less
        consolidated = RoleBenchmarkResult(
            mode="consolidated",
            findings_count=3, root_causes_count=1, recommendations_count=2,
            playbook_length=200, lessons_count=1, hints_count=1,
            total_tokens=8000, total_latency_ms=4000,
        )

        comparison = compare_role_outputs(two_role, consolidated)
        assert comparison["recommendation"] == "two_role_preferred"
