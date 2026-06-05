"""Contract tests for the reasoning segment in the autoresearch sequence format.

Teacher-reasoning distillation adds a gated ``<|reasoning|>`` segment (mirroring the
gated ``<|quality|>`` control token): the segment is emitted only when a reasoning
string is present, is byte-identical to the pre-reasoning format when absent, sits
between the (optional) quality token and ``<|strategy|>``, and completion-only loss
covers the reasoning *and* the strategy (the model must learn to generate both).
"""

from __future__ import annotations


def test_format_example_without_reasoning_is_byte_identical() -> None:
    """Golden master: omitting reasoning reproduces the existing format exactly."""
    from autocontext.training.autoresearch.sequence_format import format_example

    out = format_example(scenario="s", context="c", strategy_json='{"a": 1}', score=0.5)
    assert out == '<|scenario|>s<|context|>c<|strategy|>{"a": 1}<|score|>0.5<|end|>'


def test_format_example_with_reasoning_inserts_segment_before_strategy() -> None:
    from autocontext.training.autoresearch.sequence_format import format_example

    out = format_example(scenario="s", context="c", strategy_json='{"a": 1}', score=0.5, reasoning="use a coset")
    assert out == '<|scenario|>s<|context|>c<|reasoning|>use a coset<|strategy|>{"a": 1}<|score|>0.5<|end|>'


def test_format_example_reasoning_follows_quality() -> None:
    """When both are present, order is quality then reasoning then strategy."""
    from autocontext.training.autoresearch.sequence_format import format_example

    out = format_example(scenario="s", context="c", strategy_json="{}", score=0.5, quality=4, reasoning="r")
    assert out == "<|scenario|>s<|context|>c<|quality|>4<|reasoning|>r<|strategy|>{}<|score|>0.5<|end|>"


def test_special_tokens_include_reasoning_is_gated() -> None:
    from autocontext.training.autoresearch.sequence_format import (
        BASE_VOCAB_SIZE,
        build_special_tokens,
        total_vocab_size,
    )

    # default: no reasoning token (arch unchanged)
    assert "<|reasoning|>" not in build_special_tokens(BASE_VOCAB_SIZE)
    specials = build_special_tokens(BASE_VOCAB_SIZE, include_reasoning=True)
    assert specials["<|reasoning|>"] == BASE_VOCAB_SIZE + 5  # appended after the 5 base tokens
    assert total_vocab_size(BASE_VOCAB_SIZE, include_reasoning=True) == BASE_VOCAB_SIZE + 6


def test_special_tokens_quality_and_reasoning_have_stable_ids() -> None:
    """With both gated tokens, quality is base+5 and reasoning base+6 (base ids 0..4 stable)."""
    from autocontext.training.autoresearch.sequence_format import BASE_VOCAB_SIZE, build_special_tokens, total_vocab_size

    specials = build_special_tokens(BASE_VOCAB_SIZE, include_quality=True, include_reasoning=True)
    assert specials["<|quality|>"] == BASE_VOCAB_SIZE + 5
    assert specials["<|reasoning|>"] == BASE_VOCAB_SIZE + 6
    assert total_vocab_size(BASE_VOCAB_SIZE, include_quality=True, include_reasoning=True) == BASE_VOCAB_SIZE + 7


def test_build_generation_prompt_reason_then_construct_ends_at_reasoning() -> None:
    """In reason-then-construct mode the prompt stops at <|reasoning|> so the model
    generates the rationale first, then <|strategy|>{...}."""
    from autocontext.training.autoresearch.sequence_format import build_generation_prompt

    class _Scn:
        name = "capset"
        description = "build a set"

    assert build_generation_prompt(_Scn(), reason_then_construct=True) == (
        "<|scenario|>capset<|context|>build a set<|reasoning|>"
    )
    # default still ends at <|strategy|>
    assert build_generation_prompt(_Scn()) == "<|scenario|>capset<|context|>build a set<|strategy|>"


def test_completion_loss_mask_covers_reasoning_and_strategy() -> None:
    """With a reasoning token present, loss trains everything after <|reasoning|>
    (the rationale and the strategy), not only after <|strategy|>."""
    from autocontext.training.autoresearch.sequence_format import completion_loss_mask

    # tokens: [A, <reason>=88, R, <strat>=99, B] ; targets = [<reason>, R, <strat>, B]
    # completion starts after <|reasoning|>: R, <strat>, B are trained; <reason> is not
    mask = completion_loss_mask([5, 88, 7, 99, 8], strategy_token_id=99, reasoning_token_id=88)
    assert mask == [0, 1, 1, 1]


def test_completion_loss_mask_falls_back_to_strategy_when_no_reasoning_token() -> None:
    """Without a reasoning token in the sequence, behavior is unchanged (after strategy)."""
    from autocontext.training.autoresearch.sequence_format import completion_loss_mask

    mask = completion_loss_mask([5, 99, 7, 8], strategy_token_id=99, reasoning_token_id=88)
    assert mask == [0, 1, 1]


def test_training_example_emits_reasoning_only_when_opted_in() -> None:
    """to_sequence omits reasoning by DEFAULT so the scratch BPE corpus stays
    answer-only (its loss mask + generation prompt are not reason-aware); reasoning is
    emitted only when a consumer explicitly opts in."""
    from autocontext.training.autoresearch.sequence_format import TrainingExample

    ex = TrainingExample.from_record({"scenario": "s", "strategy": {"x": 1}, "score": 1.0, "reasoning": "because symmetry"})
    assert ex.reasoning == "because symmetry"
    assert "<|reasoning|>" not in ex.to_sequence()  # default: scratch path is answer-only
    assert "<|reasoning|>because symmetry<|strategy|>" in ex.to_sequence(include_reasoning=True)


def test_build_masked_example_reasoning_anchor_covers_reasoning_and_strategy() -> None:
    """With reasoning in the sequence, build_masked_example anchors loss on
    <|reasoning|> (train rationale + strategy), matching completion_loss_mask. This is
    the mismatch the reviewer reproduced ([0,0,0,1] vs the correct [0,1,1,1])."""
    from autocontext.training.autoresearch.sequence_format import build_masked_example

    x, y, mask = build_masked_example([5, 88, 7, 99, 8], seq_len=4, pad_token_id=0, strategy_token_id=99, reasoning_token_id=88)
    assert x == [5, 88, 7, 99]
    assert y == [88, 7, 99, 8]
    assert mask == [0, 1, 1, 1]


def test_build_masked_example_without_reasoning_token_is_strategy_anchored() -> None:
    from autocontext.training.autoresearch.sequence_format import build_masked_example

    _, _, mask = build_masked_example([5, 99, 7, 8], seq_len=3, pad_token_id=0, strategy_token_id=99)
    assert mask == [0, 1, 1]


def test_training_example_without_reasoning_is_unchanged() -> None:
    from autocontext.training.autoresearch.sequence_format import TrainingExample

    ex = TrainingExample.from_record({"scenario": "s", "strategy": {"x": 1}, "score": 1.0})
    assert ex.reasoning == ""
    assert "<|reasoning|>" not in ex.to_sequence()


def test_reasoning_token_blocked_in_structural_mask() -> None:
    """A reasoning-conditioned tokenizer must not re-emit <|reasoning|> mid-body."""
    from autocontext.training.autoresearch.sequence_format import generation_logit_mask_values, total_vocab_size

    class _Enc:
        _mergeable_ranks = {bytes([i]): i for i in range(4)}

    class _Tok:
        _encoding = _Enc()
        base_vocab_size = 16
        special_tokens = {
            "<|scenario|>": 16,
            "<|context|>": 17,
            "<|strategy|>": 18,
            "<|score|>": 19,
            "<|end|>": 20,
            "<|reasoning|>": 21,
        }

    vocab = total_vocab_size(16, include_reasoning=True)
    mask = generation_logit_mask_values(_Tok(), vocab, block_structural_specials=True)
    assert mask[21] == -1e9  # <|reasoning|> blocked during body generation
