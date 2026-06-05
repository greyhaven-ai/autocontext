"""mlx-lm completion records carry teacher reasoning (reason-then-construct).

For a pretrained instruct base the completion is the rationale followed by the
strategy JSON, so completion-only loss (``--mask-prompt``) trains the model to
reason and then construct. Absent reasoning, the completion is the bare strategy
JSON (byte-identical to answer-only training).
"""

from __future__ import annotations


def test_build_completion_record_without_reasoning_is_strategy_only() -> None:
    from autocontext.training.autoresearch.mlxlm_backend import build_completion_record

    rec = build_completion_record(task_prompt="make a cap", strategy_json='{"points": [1]}')
    assert rec == {"prompt": "make a cap", "completion": '{"points": [1]}'}


def test_build_completion_record_with_reasoning_prepends_rationale_to_completion() -> None:
    from autocontext.training.autoresearch.mlxlm_backend import build_completion_record

    rec = build_completion_record(task_prompt="make a cap", strategy_json='{"points": [1]}', reasoning="use a coset")
    assert rec["prompt"] == "make a cap"
    assert rec["completion"] == 'use a coset\n{"points": [1]}'


def test_records_to_completions_threads_reasoning_from_records() -> None:
    from autocontext.training.autoresearch.mlxlm_backend import records_to_completions

    records = [
        {"strategy": {"points": [1]}, "score": 0.9, "reasoning": "symmetry"},
        {"strategy": {"points": [2]}, "score": 0.8},  # no reasoning -> strategy-only completion
    ]
    comps = records_to_completions(records, task_prompt="T")
    assert comps[0]["completion"].startswith("symmetry\n")
    assert comps[0]["completion"].endswith('{"points": [1]}')
    assert comps[1]["completion"] == '{"points": [2]}'  # unchanged when reasoning absent


def test_curation_preserves_reasoning_field() -> None:
    """Guard: data-selection carries whole record dicts, so curation must not drop
    the reasoning field (reason-then-construct depends on it surviving dedupe/elite)."""
    from autocontext.training.autoresearch.data_selection import curate_records

    records = [
        {"strategy": {"points": [1]}, "score": 0.9, "reasoning": "keep me"},
        {"strategy": {"points": [1]}, "score": 0.5, "reasoning": "lower-score dup"},
        {"strategy": {"points": [2]}, "score": 0.3, "reasoning": "dropped by elite"},
    ]
    out = curate_records(records, elite_fraction=0.5, dedupe=True)
    assert all("reasoning" in r for r in out)
    # highest-scoring representative of the duplicate group is retained with its reasoning
    assert out[0]["reasoning"] == "keep me"


def test_extract_json_object_handles_reason_then_construct_output() -> None:
    """Reviewer F2: a reason-trained model emits `rationale\n{...}`; the assessment
    parser must recover the trailing JSON object (extract_strategy returns None on it)."""
    from autocontext.training.autoresearch.sequence_format import extract_json_object

    assert extract_json_object('use a wide spread\n{"points": [1, 2]}') == {"points": [1, 2]}
    assert extract_json_object("reason\n```json\n{\"a\": 1}\n```") == {"a": 1}
    # back-compat: still parses the strategy-token form and whole-text JSON
    assert extract_json_object('<|strategy|>{"a": 1}<|score|>0.5') == {"a": 1}
    assert extract_json_object('{"a": 1}') == {"a": 1}
    assert extract_json_object("no json here") is None


def test_extract_strategy_still_returns_none_on_reasoning_prefixed_json() -> None:
    """Characterization: the old parser cannot handle reason-then-construct (why F2 existed)."""
    from autocontext.training.autoresearch.sequence_format import extract_strategy

    assert extract_strategy('rationale\n{"points": [1]}') is None
