"""Tests for autoresearch training-data curation (pure; no MLX required)."""

from __future__ import annotations

import pytest

from autocontext.training.autoresearch.data_selection import (
    curate_records,
    dedupe_records,
    prepare_training_records,
    select_top_fraction,
)


def _rec(strategy: dict, score: float, run_id: str = "r0") -> dict:
    return {"run_id": run_id, "scenario": "s", "context": {}, "strategy": strategy, "score": score}


def test_select_top_fraction_keeps_highest_scoring() -> None:
    records = [_rec({"a": i}, score=i / 10) for i in range(10)]  # scores 0.0..0.9
    top = select_top_fraction(records, 0.3)  # ceil(10*0.3)=3
    assert len(top) == 3
    assert sorted(r["score"] for r in top) == [0.7, 0.8, 0.9]


def test_select_top_fraction_full_is_noop_preserving_order() -> None:
    records = [_rec({"a": 1}, 0.1), _rec({"a": 2}, 0.9)]
    assert select_top_fraction(records, 1.0) == records


def test_select_top_fraction_keeps_at_least_one() -> None:
    records = [_rec({"a": i}, i) for i in range(5)]
    # a valid tiny fraction still keeps at least one record
    assert len(select_top_fraction(records, 0.01)) == 1


def test_select_top_fraction_rejects_out_of_range() -> None:
    import pytest

    records = [_rec({"a": i}, i) for i in range(5)]
    for bad in (0.0, -1.0, 1.5):
        with pytest.raises(ValueError, match="fraction"):
            select_top_fraction(records, bad)


def test_dedupe_rejects_out_of_range_threshold() -> None:
    import pytest

    records = [_rec({"a": 1}, 0.5)]
    for bad in (0.0, -0.1, 1.5):
        with pytest.raises(ValueError, match="near_threshold"):
            dedupe_records(records, near_threshold=bad)


def test_dedupe_exact_keeps_highest_score() -> None:
    records = [
        _rec({"points": [1, 2, 3]}, score=0.4),
        _rec({"points": [1, 2, 3]}, score=0.9),  # exact dup, higher score
        _rec({"points": [4, 5, 6]}, score=0.5),
    ]
    deduped = dedupe_records(records)
    assert len(deduped) == 2
    by_key = {tuple(r["strategy"]["points"]): r["score"] for r in deduped}
    assert by_key[(1, 2, 3)] == 0.9  # kept the higher-scoring representative
    assert by_key[(4, 5, 6)] == 0.5


def test_dedupe_key_is_order_insensitive_for_json() -> None:
    # same dict, different key insertion order -> same canonical key -> deduped
    records = [_rec({"a": 1, "b": 2}, 0.5), _rec({"b": 2, "a": 1}, 0.6)]
    assert len(dedupe_records(records)) == 1


def test_dedupe_near_threshold_removes_near_duplicates() -> None:
    # two nearly-identical strategies (differ in the last word) + one distinct
    base = {"plan": "the quick brown fox jumps over the lazy dog near the river bank"}
    near = {"plan": "the quick brown fox jumps over the lazy dog near the river bend"}
    distinct = {"plan": "completely unrelated content about mathematics numbers 12345"}
    records = [_rec(base, 0.5), _rec(near, 0.7), _rec(distinct, 0.6)]

    exact_only = dedupe_records(records, near_threshold=1.0)
    assert len(exact_only) == 3  # all distinct exactly

    near_deduped = dedupe_records(records, near_threshold=0.6)
    # base/near collapse to one (the higher-scoring near, 0.7); distinct remains
    assert len(near_deduped) == 2
    assert any(r["strategy"] == distinct for r in near_deduped)
    collapsed = [r for r in near_deduped if r["strategy"] != distinct]
    assert collapsed[0]["score"] == 0.7


def test_curate_composes_dedupe_then_elite() -> None:
    records = [
        _rec({"points": [1]}, 0.2),
        _rec({"points": [1]}, 0.3),  # dup of above
        _rec({"points": [2]}, 0.9),
        _rec({"points": [3]}, 0.5),
    ]
    out = curate_records(records, elite_fraction=0.5, dedupe=True)
    # dedupe -> 3 unique ([1]=0.3, [2]=0.9, [3]=0.5); elite 0.5 -> ceil(3*0.5)=2 best
    assert len(out) == 2
    assert sorted(r["score"] for r in out) == [0.5, 0.9]


def test_curate_default_is_noop() -> None:
    records = [_rec({"a": 1}, 0.1), _rec({"a": 1}, 0.2)]
    assert curate_records(records) == records


def test_prepare_training_records_no_augmenter_equals_curate() -> None:
    """Empty augmenter spec makes prepare_training_records delegate to curate_records."""
    records = [_rec({"a": 1}, 0.1), _rec({"a": 2}, 0.9), _rec({"a": 1}, 0.1)]
    assert prepare_training_records(records, elite_fraction=0.5, dedupe=True) == curate_records(
        records, elite_fraction=0.5, dedupe=True
    )


def test_prepare_training_records_resolves_and_applies_augmenter_then_curates() -> None:
    """A real 'module:function' spec is resolved, applied, then the result is curated.

    `copy:deepcopy` is a stand-in augmenter: it returns the records (deep-copied), so
    the resolve -> apply -> curate chain runs end-to-end with a real import. After the
    augmenter, dedupe collapses the duplicate construction down to one representative.
    """
    records = [_rec({"a": 1}, 0.1), _rec({"a": 1}, 0.2)]
    out = prepare_training_records(records, augmenter_spec="copy:deepcopy", dedupe=True)
    assert len(out) == 1  # dedupe kept the single highest-scoring representative
    assert out[0]["score"] == 0.2


def test_prepare_training_records_propagates_bad_spec() -> None:
    with pytest.raises(ValueError, match="package.module:function"):
        prepare_training_records([_rec({"a": 1}, 0.1)], augmenter_spec="nocolon")
