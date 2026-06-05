"""Contract tests for the autoresearch sequence-format domain (PR1).

These pin the training-data sequence format byte-for-byte (golden master) so the
DDD consolidation that moves the contract into ``sequence_format`` and removes the
``cuda.py`` duplication cannot change observable behavior. They also cover the new
single-source ``build_generation_prompt`` and ``TrainingExample`` value object.
"""

from __future__ import annotations

import json

import pytest

# ---------------------------------------------------------------------------
# Golden master: the format must remain byte-identical (imported via the public
# prepare surface, which re-exports the contract for backward compatibility).
# ---------------------------------------------------------------------------


def test_format_example_is_byte_stable_via_prepare() -> None:
    from autocontext.training.autoresearch.prepare import format_example

    out = format_example(scenario="grid_ctf", context="ctx", strategy_json='{"a": 1}', score=0.5)
    assert out == '<|scenario|>grid_ctf<|context|>ctx<|strategy|>{"a": 1}<|score|>0.5<|end|>'


def test_extract_strategy_round_trips_via_prepare() -> None:
    from autocontext.training.autoresearch.prepare import _extract_strategy_json

    assert _extract_strategy_json('<|strategy|>{"a": 1}<|score|>0.5<|end|>') == {"a": 1}
    assert _extract_strategy_json('<|strategy|>{"a": 1}') == {"a": 1}
    assert _extract_strategy_json("not json at all") is None


def test_special_tokens_and_vocab_via_prepare() -> None:
    from autocontext.training.autoresearch.prepare import (
        BASE_VOCAB_SIZE,
        SPECIAL_TOKEN_STRINGS,
        build_special_tokens,
        total_vocab_size,
    )

    # Default: the original five tokens only (quality is gated -> default arch unchanged).
    specials = build_special_tokens(BASE_VOCAB_SIZE)
    assert specials == {
        "<|scenario|>": BASE_VOCAB_SIZE + 0,
        "<|context|>": BASE_VOCAB_SIZE + 1,
        "<|strategy|>": BASE_VOCAB_SIZE + 2,
        "<|score|>": BASE_VOCAB_SIZE + 3,
        "<|end|>": BASE_VOCAB_SIZE + 4,
    }
    assert "<|quality|>" not in specials
    assert total_vocab_size(BASE_VOCAB_SIZE) == BASE_VOCAB_SIZE + len(SPECIAL_TOKEN_STRINGS)


def test_special_tokens_include_quality_is_gated() -> None:
    from autocontext.training.autoresearch.prepare import BASE_VOCAB_SIZE, build_special_tokens, total_vocab_size

    specials = build_special_tokens(BASE_VOCAB_SIZE, include_quality=True)
    assert specials["<|quality|>"] == BASE_VOCAB_SIZE + 5  # appended last, ids 0..4 stable
    assert total_vocab_size(BASE_VOCAB_SIZE, include_quality=True) == BASE_VOCAB_SIZE + 6
    # default stays at the original size
    assert total_vocab_size(BASE_VOCAB_SIZE) == BASE_VOCAB_SIZE + 5


def test_generation_logit_mask_values_via_prepare_unchanged() -> None:
    from autocontext.training.autoresearch.prepare import generation_logit_mask_values, total_vocab_size

    class _Enc:
        _mergeable_ranks = {bytes([i]): i for i in range(4)}

    class _Tok:
        _encoding = _Enc()
        base_vocab_size = 16

    vocab = total_vocab_size(16)
    mask = generation_logit_mask_values(_Tok(), vocab, block_structural_specials=True)
    assert mask[0] == 0.0 and mask[3] == 0.0
    assert mask[4] == -1e9 and mask[15] == -1e9  # phantom gap
    assert mask[16] == -1e9  # <|scenario|> blocked
    assert mask[19] == 0.0 and mask[20] == 0.0  # score/end allowed


# ---------------------------------------------------------------------------
# New single-source contract API (sequence_format).
# ---------------------------------------------------------------------------


def test_build_generation_prompt_single_source() -> None:
    from autocontext.training.autoresearch.sequence_format import build_generation_prompt

    class _Scn:
        name = "capset"
        description = "build a set"

    assert build_generation_prompt(_Scn()) == "<|scenario|>capset<|context|>build a set<|strategy|>"


def test_build_generation_prompt_falls_back_to_classname() -> None:
    from autocontext.training.autoresearch.sequence_format import build_generation_prompt

    class WidgetTask:  # no name attr, no description
        pass

    # name falls back to lowercased class name; context falls back to "" when no description
    assert build_generation_prompt(WidgetTask()) == "<|scenario|>widgettask<|context|><|strategy|>"


def test_training_example_to_sequence_matches_format_example() -> None:
    from autocontext.training.autoresearch.sequence_format import TrainingExample, format_example

    record = {
        "run_id": "r1",
        "scenario": "grid_ctf",
        "context": {"playbook": "p"},
        "strategy": {"a": 1, "b": 2},
        "score": 0.75,
    }
    ex = TrainingExample.from_record(record)
    expected = format_example(
        scenario="grid_ctf",
        context=json.dumps({"playbook": "p"}, sort_keys=True),
        strategy_json=json.dumps({"a": 1, "b": 2}, sort_keys=True),
        score=0.75,
    )
    assert ex.to_sequence() == expected


def test_training_example_defaults_missing_context() -> None:
    from autocontext.training.autoresearch.sequence_format import TrainingExample

    ex = TrainingExample.from_record({"scenario": "s", "strategy": {"x": 1}, "score": 1.0})
    assert ex.context == json.dumps({}, sort_keys=True)


def test_completion_loss_mask_trains_only_after_strategy() -> None:
    from autocontext.training.autoresearch.sequence_format import completion_loss_mask

    # tokens: [A, <strat>, B, C] with strategy id = 99
    # targets (token_ids[1:]) = [<strat>, B, C]; only B, C are completion targets
    mask = completion_loss_mask([5, 99, 7, 8], strategy_token_id=99)
    assert mask == [0, 1, 1]


def test_completion_loss_mask_all_ones_when_strategy_absent() -> None:
    from autocontext.training.autoresearch.sequence_format import completion_loss_mask

    assert completion_loss_mask([1, 2, 3, 4], strategy_token_id=99) == [1, 1, 1]


def test_completion_loss_mask_short_sequences() -> None:
    from autocontext.training.autoresearch.sequence_format import completion_loss_mask

    assert completion_loss_mask([], strategy_token_id=99) == []
    assert completion_loss_mask([99], strategy_token_id=99) == []


def test_build_masked_example_no_pad() -> None:
    from autocontext.training.autoresearch.sequence_format import build_masked_example

    # tokens [A, <strat>=99, B, C], seq_len 3 -> exactly fills, no padding
    x, y, mask = build_masked_example([5, 99, 7, 8], seq_len=3, pad_token_id=0, strategy_token_id=99)
    assert x == [5, 99, 7]
    assert y == [99, 7, 8]
    assert mask == [0, 1, 1]  # train only on completion targets


def test_build_masked_example_pads_and_masks_padding() -> None:
    from autocontext.training.autoresearch.sequence_format import build_masked_example

    x, y, mask = build_masked_example([5, 99, 7], seq_len=4, pad_token_id=0, strategy_token_id=99)
    assert x == [5, 99, 0, 0]
    assert y == [99, 7, 0, 0]
    assert mask == [0, 1, 0, 0]  # prompt (0) + completion (1) + padding (0,0)


def test_build_masked_example_truncates_keeping_tail() -> None:
    from autocontext.training.autoresearch.sequence_format import build_masked_example

    # 6 tokens, seq_len 3 -> keep last seq_len+1 = 4 tokens
    x, y, mask = build_masked_example([1, 2, 3, 99, 5, 6], seq_len=3, pad_token_id=0, strategy_token_id=99)
    assert len(x) == 3 and len(y) == 3 and len(mask) == 3
    assert x == [3, 99, 5] and y == [99, 5, 6]


def test_build_masked_example_too_short_returns_none() -> None:
    from autocontext.training.autoresearch.sequence_format import build_masked_example

    assert build_masked_example([7], seq_len=4, pad_token_id=0, strategy_token_id=99) is None


def test_score_to_quality_bucket() -> None:
    from autocontext.training.autoresearch.sequence_format import score_to_quality_bucket

    assert score_to_quality_bucket(0.0, num_buckets=5) == 0
    assert score_to_quality_bucket(1.0, num_buckets=5) == 4  # top bucket (clamped from index 5)
    assert score_to_quality_bucket(0.5, num_buckets=5) == 2
    # out-of-range clamps into [0, num_buckets-1]
    assert score_to_quality_bucket(-1.0, num_buckets=5) == 0
    assert score_to_quality_bucket(2.0, num_buckets=5) == 4


def test_format_example_without_quality_is_unchanged() -> None:
    from autocontext.training.autoresearch.sequence_format import format_example

    # golden master: omitting quality reproduces the pre-conditioning format exactly
    out = format_example(scenario="s", context="c", strategy_json='{"a": 1}', score=0.5)
    assert out == '<|scenario|>s<|context|>c<|strategy|>{"a": 1}<|score|>0.5<|end|>'


def test_format_example_with_quality_inserts_control_token() -> None:
    from autocontext.training.autoresearch.sequence_format import format_example

    out = format_example(scenario="s", context="c", strategy_json='{"a": 1}', score=0.5, quality=4)
    assert out == '<|scenario|>s<|context|>c<|quality|>4<|strategy|>{"a": 1}<|score|>0.5<|end|>'


def test_build_generation_prompt_with_target_quality() -> None:
    from autocontext.training.autoresearch.sequence_format import build_generation_prompt

    class _Scn:
        name = "capset"
        description = "build a set"

    assert build_generation_prompt(_Scn()) == "<|scenario|>capset<|context|>build a set<|strategy|>"
    assert build_generation_prompt(_Scn(), target_quality=4) == "<|scenario|>capset<|context|>build a set<|quality|>4<|strategy|>"


def test_training_example_score_conditioned_emits_quality() -> None:
    from autocontext.training.autoresearch.sequence_format import TrainingExample

    ex = TrainingExample.from_record({"scenario": "s", "strategy": {"x": 1}, "score": 1.0})
    plain = ex.to_sequence()
    conditioned = ex.to_sequence(score_conditioned=True, num_buckets=5)
    assert "<|quality|>" not in plain
    assert "<|quality|>4" in conditioned  # score 1.0 -> top bucket


def test_quality_token_blocked_in_structural_mask() -> None:
    from autocontext.training.autoresearch.sequence_format import (
        build_special_tokens,
        generation_logit_mask_values,
        total_vocab_size,
    )

    # A score-conditioned tokenizer exposes the quality token in special_tokens, so
    # the mask (which reads tokenizer.special_tokens) blocks it during body generation.
    vocab = total_vocab_size(16, include_quality=True)
    quality_id = build_special_tokens(16, include_quality=True)["<|quality|>"]
    mask = generation_logit_mask_values(_FakeTok16(), vocab, block_structural_specials=True)
    assert mask[quality_id] == -1e9


class _FakeTok16:
    class _Enc:
        _mergeable_ranks = {bytes([i]): i for i in range(4)}

    _encoding = _Enc()
    base_vocab_size = 16
    # score-conditioned special-token map (base 16): ids 16..21
    special_tokens = {
        "<|scenario|>": 16,
        "<|context|>": 17,
        "<|strategy|>": 18,
        "<|score|>": 19,
        "<|end|>": 20,
        "<|quality|>": 21,
    }


def test_cuda_uses_shared_contract_no_duplicate_definitions() -> None:
    """cuda.py must not redefine the parser/resolvers; it consumes sequence_format."""
    import inspect

    from autocontext.training.autoresearch import cuda

    src = inspect.getsource(cuda)
    assert "def _extract_strategy_json" not in src
    assert "def _resolve_scenario_name" not in src
    assert "def _resolve_scenario_context" not in src


# ---------------------------------------------------------------------------
# Reward-weighted regression: per-example loss weights from scores (PR3b)
# ---------------------------------------------------------------------------


def test_score_loss_weights_uniform_is_all_ones() -> None:
    """Default mode is byte-identical to unweighted training (all 1.0)."""
    from autocontext.training.autoresearch.sequence_format import score_loss_weights

    assert score_loss_weights([0.1, 0.9, 0.5], mode="uniform") == [1.0, 1.0, 1.0]
    assert score_loss_weights([], mode="uniform") == []


def test_score_loss_weights_unknown_mode_raises() -> None:
    from autocontext.training.autoresearch.sequence_format import score_loss_weights

    with pytest.raises(ValueError, match="unknown loss-weight mode"):
        score_loss_weights([0.1, 0.9], mode="bogus")


def test_score_loss_weights_linear_orders_and_means_one() -> None:
    """Linear mode is monotone in score and mean-normalized (preserves effective LR)."""
    from autocontext.training.autoresearch.sequence_format import score_loss_weights

    w = score_loss_weights([0.0, 0.5, 1.0], mode="linear")
    assert w[0] < w[1] < w[2]  # higher score -> higher weight
    assert abs(sum(w) / len(w) - 1.0) < 1e-9  # mean weight is 1.0


def test_score_loss_weights_constant_scores_fall_back_to_uniform() -> None:
    """No score spread -> nothing to weight -> uniform (no divide-by-zero)."""
    from autocontext.training.autoresearch.sequence_format import score_loss_weights

    assert score_loss_weights([0.7, 0.7, 0.7], mode="linear") == [1.0, 1.0, 1.0]
    assert score_loss_weights([0.7, 0.7, 0.7], mode="softmax") == [1.0, 1.0, 1.0]


def test_score_loss_weights_softmax_means_one_and_orders() -> None:
    from autocontext.training.autoresearch.sequence_format import score_loss_weights

    w = score_loss_weights([0.0, 1.0], mode="softmax", temperature=0.5)
    assert w[1] > w[0]
    assert abs(sum(w) / len(w) - 1.0) < 1e-9


def test_score_loss_weights_softmax_high_temperature_approaches_uniform() -> None:
    """Large temperature flattens the softmax toward uniform (continuous knob)."""
    from autocontext.training.autoresearch.sequence_format import score_loss_weights

    w = score_loss_weights([0.0, 0.5, 1.0], mode="softmax", temperature=1e6)
    for wi in w:
        assert abs(wi - 1.0) < 1e-3


def test_score_loss_weights_softmax_rejects_non_positive_temperature() -> None:
    """softmax temperature <= 0 is rejected (no silent coercion to 1.0)."""
    from autocontext.training.autoresearch.sequence_format import score_loss_weights

    for bad in (0.0, -1.0):
        with pytest.raises(ValueError, match="temperature must be > 0"):
            score_loss_weights([0.0, 1.0], mode="softmax", temperature=bad)
    # linear/uniform do not use temperature, so a 0.0 there is harmless
    assert score_loss_weights([0.0, 1.0], mode="linear", temperature=0.0)


def test_score_loss_weights_softmax_low_temperature_concentrates_on_max() -> None:
    """Small temperature concentrates weight on the top-scoring example (soft elite)."""
    from autocontext.training.autoresearch.sequence_format import score_loss_weights

    w = score_loss_weights([0.0, 0.5, 1.0], mode="softmax", temperature=0.01)
    assert w[2] > w[0] and w[2] > w[1]
    assert w[2] > 2.5  # near n=3 (all mass on the max)
