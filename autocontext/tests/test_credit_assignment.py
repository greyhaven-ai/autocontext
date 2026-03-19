"""Tests for AC-199: component sensitivity profiling and credit assignment.

Covers: ComponentChange, GenerationChangeVector, AttributionResult,
CreditAssignmentRecord, compute_change_vector, attribute_credit,
format_attribution_for_agent, summarize_credit_patterns.
"""

from __future__ import annotations

# ===========================================================================
# ComponentChange
# ===========================================================================


class TestComponentChange:
    def test_construction(self) -> None:
        from autocontext.analytics.credit_assignment import ComponentChange

        change = ComponentChange(
            component="playbook",
            magnitude=0.45,
            description="Major playbook rewrite (450 chars changed)",
        )
        assert change.component == "playbook"
        assert change.magnitude == 0.45

    def test_roundtrip(self) -> None:
        from autocontext.analytics.credit_assignment import ComponentChange

        change = ComponentChange(component="tools", magnitude=0.2, description="1 tool added")
        d = change.to_dict()
        restored = ComponentChange.from_dict(d)
        assert restored.component == "tools"
        assert restored.magnitude == 0.2


# ===========================================================================
# GenerationChangeVector
# ===========================================================================


class TestGenerationChangeVector:
    def test_construction(self) -> None:
        from autocontext.analytics.credit_assignment import (
            ComponentChange,
            GenerationChangeVector,
        )

        vec = GenerationChangeVector(
            generation=5,
            score_delta=0.08,
            changes=[
                ComponentChange("playbook", 0.6, "major rewrite"),
                ComponentChange("tools", 0.2, "1 tool added"),
                ComponentChange("hints", 0.1, "1 hint updated"),
            ],
        )
        assert vec.generation == 5
        assert len(vec.changes) == 3

    def test_total_change(self) -> None:
        from autocontext.analytics.credit_assignment import (
            ComponentChange,
            GenerationChangeVector,
        )

        vec = GenerationChangeVector(
            generation=3,
            score_delta=0.05,
            changes=[
                ComponentChange("playbook", 0.4, ""),
                ComponentChange("tools", 0.3, ""),
            ],
        )
        assert vec.total_change_magnitude == 0.7

    def test_roundtrip(self) -> None:
        from autocontext.analytics.credit_assignment import (
            ComponentChange,
            GenerationChangeVector,
        )

        vec = GenerationChangeVector(
            generation=2, score_delta=0.1,
            changes=[ComponentChange("playbook", 0.5, "changed")],
        )
        d = vec.to_dict()
        restored = GenerationChangeVector.from_dict(d)
        assert restored.generation == 2
        assert len(restored.changes) == 1


# ===========================================================================
# compute_change_vector
# ===========================================================================


class TestComputeChangeVector:
    def test_detects_playbook_change(self) -> None:
        from autocontext.analytics.credit_assignment import compute_change_vector

        prev = {"playbook": "Old playbook content", "tools": ["tool_a"], "hints": "hint 1"}
        curr = {"playbook": "Completely new playbook with different strategy", "tools": ["tool_a"], "hints": "hint 1"}

        vec = compute_change_vector(
            generation=3,
            score_delta=0.05,
            previous_state=prev,
            current_state=curr,
        )
        playbook_change = next((c for c in vec.changes if c.component == "playbook"), None)
        assert playbook_change is not None
        assert playbook_change.magnitude > 0

    def test_detects_tool_addition(self) -> None:
        from autocontext.analytics.credit_assignment import compute_change_vector

        prev = {"playbook": "same", "tools": ["tool_a"], "hints": "same"}
        curr = {"playbook": "same", "tools": ["tool_a", "tool_b"], "hints": "same"}

        vec = compute_change_vector(generation=4, score_delta=0.03, previous_state=prev, current_state=curr)
        tools_change = next((c for c in vec.changes if c.component == "tools"), None)
        assert tools_change is not None
        assert tools_change.magnitude > 0

    def test_no_changes_zero_magnitude(self) -> None:
        from autocontext.analytics.credit_assignment import compute_change_vector

        state = {"playbook": "same", "tools": ["a"], "hints": "same"}
        vec = compute_change_vector(generation=2, score_delta=0.0, previous_state=state, current_state=state)
        assert vec.total_change_magnitude == 0.0

    def test_detects_analysis_change(self) -> None:
        from autocontext.analytics.credit_assignment import compute_change_vector

        prev = {"analysis": "Lean into offense."}
        curr = {"analysis": "Defense is the real bottleneck."}

        vec = compute_change_vector(generation=3, score_delta=0.04, previous_state=prev, current_state=curr)
        analysis_change = next((c for c in vec.changes if c.component == "analysis"), None)
        assert analysis_change is not None
        assert analysis_change.magnitude > 0


# ===========================================================================
# attribute_credit
# ===========================================================================


class TestAttributeCredit:
    def test_proportional_attribution(self) -> None:
        from autocontext.analytics.credit_assignment import (
            ComponentChange,
            GenerationChangeVector,
            attribute_credit,
        )

        vec = GenerationChangeVector(
            generation=5, score_delta=0.10,
            changes=[
                ComponentChange("playbook", 0.6, "major change"),
                ComponentChange("tools", 0.2, "minor change"),
                ComponentChange("hints", 0.2, "minor change"),
            ],
        )
        result = attribute_credit(vec)
        assert result.total_delta == 0.10
        # Playbook had 60% of changes → should get ~60% of credit
        assert result.credits["playbook"] > result.credits["tools"]

    def test_zero_delta_zero_credit(self) -> None:
        from autocontext.analytics.credit_assignment import (
            ComponentChange,
            GenerationChangeVector,
            attribute_credit,
        )

        vec = GenerationChangeVector(
            generation=3, score_delta=0.0,
            changes=[ComponentChange("playbook", 0.5, "changed but no improvement")],
        )
        result = attribute_credit(vec)
        assert all(v == 0.0 for v in result.credits.values())

    def test_empty_changes(self) -> None:
        from autocontext.analytics.credit_assignment import (
            GenerationChangeVector,
            attribute_credit,
        )

        vec = GenerationChangeVector(generation=1, score_delta=0.05, changes=[])
        result = attribute_credit(vec)
        assert len(result.credits) == 0

    def test_roundtrip(self) -> None:
        from autocontext.analytics.credit_assignment import AttributionResult

        result = AttributionResult(
            generation=2,
            total_delta=0.08,
            credits={"playbook": 0.05, "tools": 0.03},
            metadata={"gate_decision": "advance"},
        )
        restored = AttributionResult.from_dict(result.to_dict())
        assert restored.generation == 2
        assert restored.credits["playbook"] == 0.05
        assert restored.metadata["gate_decision"] == "advance"


class TestCreditAssignmentRecord:
    def test_roundtrip(self) -> None:
        from autocontext.analytics.credit_assignment import (
            AttributionResult,
            ComponentChange,
            CreditAssignmentRecord,
            GenerationChangeVector,
        )

        record = CreditAssignmentRecord(
            run_id="run-123",
            generation=4,
            vector=GenerationChangeVector(
                generation=4,
                score_delta=0.1,
                changes=[ComponentChange("playbook", 0.6, "changed")],
            ),
            attribution=AttributionResult(
                generation=4,
                total_delta=0.1,
                credits={"playbook": 0.1},
            ),
            metadata={"scenario_name": "grid_ctf"},
        )
        restored = CreditAssignmentRecord.from_dict(record.to_dict())
        assert restored.run_id == "run-123"
        assert restored.vector.changes[0].component == "playbook"
        assert restored.metadata["scenario_name"] == "grid_ctf"


# ===========================================================================
# format_attribution_for_agent
# ===========================================================================


class TestFormatAttributionForAgent:
    def test_formats_for_analyst(self) -> None:
        from autocontext.analytics.credit_assignment import (
            AttributionResult,
            format_attribution_for_agent,
        )

        result = AttributionResult(
            generation=5,
            total_delta=0.10,
            credits={"playbook": 0.06, "tools": 0.02, "hints": 0.02},
        )
        text = format_attribution_for_agent(result, role="analyst")
        assert "Previous Analysis Attribution" in text
        assert "playbook" in text.lower()
        assert "60%" in text or "0.06" in text

    def test_empty_credits_returns_empty(self) -> None:
        from autocontext.analytics.credit_assignment import (
            AttributionResult,
            format_attribution_for_agent,
        )

        result = AttributionResult(generation=1, total_delta=0.0, credits={})
        text = format_attribution_for_agent(result, role="analyst")
        assert text == ""

    def test_role_specific_output_differs(self) -> None:
        from autocontext.analytics.credit_assignment import (
            AttributionResult,
            format_attribution_for_agent,
        )

        result = AttributionResult(
            generation=5,
            total_delta=0.10,
            credits={"analysis": 0.03, "playbook": 0.04, "tools": 0.03},
        )
        analyst_text = format_attribution_for_agent(result, role="analyst")
        architect_text = format_attribution_for_agent(result, role="architect")
        assert analyst_text != architect_text
        assert "Previous Analysis Attribution" in analyst_text
        assert "Previous Tooling Attribution" in architect_text


class TestSummarizeCreditPatterns:
    def test_rolls_up_component_patterns(self) -> None:
        from autocontext.analytics.credit_assignment import (
            AttributionResult,
            ComponentChange,
            CreditAssignmentRecord,
            GenerationChangeVector,
            summarize_credit_patterns,
        )

        records = [
            CreditAssignmentRecord(
                run_id="run-1",
                generation=1,
                vector=GenerationChangeVector(
                    generation=1,
                    score_delta=0.10,
                    changes=[ComponentChange("playbook", 0.6, "changed")],
                ),
                attribution=AttributionResult(
                    generation=1,
                    total_delta=0.10,
                    credits={"playbook": 0.10},
                ),
            ),
            CreditAssignmentRecord(
                run_id="run-2",
                generation=2,
                vector=GenerationChangeVector(
                    generation=2,
                    score_delta=0.05,
                    changes=[ComponentChange("tools", 0.5, "tool added")],
                ),
                attribution=AttributionResult(
                    generation=2,
                    total_delta=0.05,
                    credits={"tools": 0.05},
                ),
            ),
        ]

        summary = summarize_credit_patterns(records)
        assert summary["total_records"] == 2
        assert summary["run_count"] == 2
        assert summary["components"][0]["component"] == "playbook"
