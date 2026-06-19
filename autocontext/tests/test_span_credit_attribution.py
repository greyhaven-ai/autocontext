from __future__ import annotations

import importlib
from typing import Any


def _credit() -> Any:
    return importlib.import_module("autocontext.analytics.credit_assignment")


def test_span_ids_are_stable_for_same_source_and_text() -> None:
    credit = _credit()
    left = credit.extract_knowledge_spans("hints", "- Check invariant\n- Try exact route")
    right = credit.extract_knowledge_spans("hints", "  Check invariant\n\nTry exact route")

    assert [span.span_id for span in left] == [span.span_id for span in right]
    assert left[0].metadata["source"] == "hints"
    assert left[0].metadata["line_number"] == 1


def test_span_attribution_records_correlative_span_credit() -> None:
    credit = _credit()
    vector = credit.GenerationChangeVector(
        generation=3,
        score_delta=0.3,
        changes=[credit.ComponentChange(component="hints", magnitude=1.0, description="Hints changed")],
    )
    attribution = credit.attribute_credit(vector)
    report = credit.build_span_attribution(
        vector,
        attribution,
        current_state={"hints": "- Check invariant\n- Verify repair"},
    )

    assert report["schema_version"] == 1
    assert report["mode"] == "span"
    assert report["spans"][0]["credit"] == 0.15
    assert report["spans"][0]["evidence_level"] == "component_correlated"
    assert report["spans"][0]["metadata"]["source"] == "hints"


def test_span_ranker_demotes_low_or_negative_credit_without_deleting() -> None:
    credit = _credit()
    spans = credit.extract_knowledge_spans("playbook", "keep me\ndemote me")
    ranked = credit.rank_spans_by_credit(spans, {spans[0].span_id: 0.2, spans[1].span_id: -0.1})

    assert [item["text"] for item in ranked] == ["keep me", "demote me"]
    assert ranked[1]["demoted"] is True


def test_format_attribution_includes_span_context_when_present() -> None:
    credit = _credit()
    result = credit.AttributionResult(
        generation=4,
        total_delta=0.2,
        credits={"hints": 0.2},
        metadata={
            "context_attribution": "span",
            "span_attribution": {
                "schema_version": 1,
                "mode": "span",
                "spans": [
                    {
                        "span_id": "hints:abc",
                        "source": "hints",
                        "text": "Check invariant",
                        "credit": 0.2,
                        "evidence_level": "component_correlated",
                        "metadata": {"line_number": 1},
                    }
                ],
            },
        },
    )

    formatted = credit.format_attribution_for_agent(result, "coach")

    assert "Span attribution (component-correlated, noisy)" in formatted
    assert "Check invariant" in formatted


def test_regression_span_attribution_reaches_prompt_summary() -> None:
    credit = _credit()
    vector = credit.GenerationChangeVector(
        generation=5,
        score_delta=-0.2,
        changes=[credit.ComponentChange(component="hints", magnitude=1.0, description="Hints changed")],
    )
    attribution = credit.attribute_credit(vector)
    report = credit.build_span_attribution(
        vector,
        attribution,
        current_state={"hints": "Regressed hint"},
    )
    result = credit.AttributionResult(
        generation=5,
        total_delta=-0.2,
        credits=attribution.credits,
        metadata={"context_attribution": "span", "span_attribution": report},
    )

    formatted = credit.format_attribution_for_agent(result, "coach")

    assert "Total score delta: -0.2000" in formatted
    assert "demotion candidates" in formatted
    assert "Regressed hint" in formatted
    assert "-0.2000" in formatted
