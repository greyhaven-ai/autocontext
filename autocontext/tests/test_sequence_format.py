"""Contract tests for the autoresearch sequence-format domain (PR1).

These pin the training-data sequence format byte-for-byte (golden master) so the
DDD consolidation that moves the contract into ``sequence_format`` and removes the
``cuda.py`` duplication cannot change observable behavior. They also cover the new
single-source ``build_generation_prompt`` and ``TrainingExample`` value object.
"""

from __future__ import annotations

import json

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

    specials = build_special_tokens(BASE_VOCAB_SIZE)
    assert specials == {
        "<|scenario|>": BASE_VOCAB_SIZE + 0,
        "<|context|>": BASE_VOCAB_SIZE + 1,
        "<|strategy|>": BASE_VOCAB_SIZE + 2,
        "<|score|>": BASE_VOCAB_SIZE + 3,
        "<|end|>": BASE_VOCAB_SIZE + 4,
    }
    assert total_vocab_size(BASE_VOCAB_SIZE) == BASE_VOCAB_SIZE + len(SPECIAL_TOKEN_STRINGS)


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


def test_cuda_uses_shared_contract_no_duplicate_definitions() -> None:
    """cuda.py must not redefine the parser/resolvers; it consumes sequence_format."""
    import inspect

    from autocontext.training.autoresearch import cuda

    src = inspect.getsource(cuda)
    assert "def _extract_strategy_json" not in src
    assert "def _resolve_scenario_name" not in src
    assert "def _resolve_scenario_context" not in src
