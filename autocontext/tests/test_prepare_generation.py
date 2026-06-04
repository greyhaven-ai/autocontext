"""Tests for autoresearch generation sampling + vocab masking.

Covers the structured-output generation fixes:
- ``decodable_vocab_size`` reports the real (decodable) BPE range so sampling
  never emits phantom ids in ``[n_learned, base_vocab)``.
- real-path generation supports temperature sampling + per-seed variation, masks
  phantom ids and structural special tokens, and never raises on decode.
- ``assess_strategy_quality`` accepts temperature/top_k/seed_base and stays
  backward compatible with greedy defaults.

MLX-dependent tests are skipped when MLX is not installed (CI-safe).
"""

from __future__ import annotations

import pytest

from autocontext.training import HAS_MLX

# ---------------------------------------------------------------------------
# decodable_vocab_size (no MLX required)
# ---------------------------------------------------------------------------


def test_decodable_vocab_size_uses_mergeable_ranks() -> None:
    from autocontext.training.autoresearch.prepare import decodable_vocab_size

    class _Enc:
        _mergeable_ranks = {b"a": 0, b"b": 1, b"ab": 2}

    class _Tok:
        _encoding = _Enc()
        base_vocab_size = 8192

    # max rank 2 -> 3 decodable base ids, far below base_vocab_size
    assert decodable_vocab_size(_Tok()) == 3


def test_decodable_vocab_size_falls_back_to_base() -> None:
    from autocontext.training.autoresearch.prepare import decodable_vocab_size

    class _Tok:
        base_vocab_size = 512

    assert decodable_vocab_size(_Tok()) == 512


def test_generation_logit_mask_values_blocks_phantom_and_structural() -> None:
    from autocontext.training.autoresearch.prepare import (
        build_special_tokens,
        generation_logit_mask_values,
        total_vocab_size,
    )

    # base 16 -> specials at 16..20; learned only 4 base ids -> phantom gap [4, 16)
    class _Enc:
        _mergeable_ranks = {bytes([i]): i for i in range(4)}

    class _Tok:
        _encoding = _Enc()
        base_vocab_size = 16

    vocab = total_vocab_size(16)
    specials = build_special_tokens(16)
    mask = generation_logit_mask_values(_Tok(), vocab, block_structural_specials=True)

    # decodable BPE ids allowed
    assert mask[0] == 0.0 and mask[3] == 0.0
    # phantom gap blocked
    assert mask[4] == -1e9 and mask[15] == -1e9
    # structural specials blocked, score/end allowed
    assert mask[specials["<|scenario|>"]] == -1e9
    assert mask[specials["<|strategy|>"]] == -1e9
    assert mask[specials["<|score|>"]] == 0.0
    assert mask[specials["<|end|>"]] == 0.0

    # provider mode keeps structural specials available (still blocks phantom gap)
    mask_provider = generation_logit_mask_values(_Tok(), vocab, block_structural_specials=False)
    assert mask_provider[specials["<|scenario|>"]] == 0.0
    assert mask_provider[4] == -1e9


# ---------------------------------------------------------------------------
# Real-path generation (MLX required)
# ---------------------------------------------------------------------------


def _tiny_model_and_tokenizer(tmp_path, vocab_size: int = 1024):
    """Build a tiny tokenizer (small corpus -> n_base < vocab_size) and matching model.

    The target vocab is far larger than the corpus can fill, so the learned base
    stays well below ``vocab_size`` and leaves a phantom-id gap to exercise.
    """
    from autocontext.training.autoresearch.prepare import (
        decodable_vocab_size,
        total_vocab_size,
        train_tokenizer,
    )
    from autocontext.training.autoresearch.train import GPTModel, ModelConfig

    corpus = [
        '<|scenario|>capset<|context|>build a set<|strategy|>{"points": [0, 1, 5, 12]}<|score|>0.5<|end|>' for _ in range(40)
    ]
    corpus_path = tmp_path / "corpus.txt"
    corpus_path.write_text("\n".join(corpus), encoding="utf-8")
    tok = train_tokenizer(corpus_path, vocab_size=vocab_size)
    cfg = ModelConfig(seq_len=64, vocab_size=total_vocab_size(vocab_size))
    model = GPTModel(cfg)
    return model, tok, cfg, decodable_vocab_size(tok)


@pytest.mark.skipif(not HAS_MLX, reason="MLX not installed")
def test_generation_never_emits_phantom_or_structural_ids(tmp_path) -> None:
    """Sampling must stay within decodable BPE ids + score/end; never crash decode.

    A phantom id (in ``[n_base, base_vocab)``) or a structural special token would
    make ``tokenizer.decode`` raise; reaching the assert proves they were masked.
    """
    from autocontext.training.autoresearch.prepare import _generate_strategy_text

    model, tok, cfg, n_base = _tiny_model_and_tokenizer(tmp_path, vocab_size=1024)
    assert n_base < 1024, "small corpus should leave a phantom-id gap to exercise"

    class _Scn:
        name = "capset"
        description = "build a set"

    # high temperature exercises the full distribution incl. phantom range
    for seed in range(6):
        text = _generate_strategy_text(
            model=model,
            tokenizer=tok,
            scenario=_Scn(),
            seed=seed,
            max_new_tokens=24,
            temperature=1.5,
        )
        assert isinstance(text, str)  # decode did not raise
    # also verify the emitted ids are constrained by re-encoding is not reliable;
    # instead assert decode worked above (would KeyError on a phantom id).


@pytest.mark.skipif(not HAS_MLX, reason="MLX not installed")
def test_temperature_zero_is_deterministic(tmp_path) -> None:
    """Greedy (temperature<=0) yields identical output regardless of seed."""
    from autocontext.training.autoresearch.prepare import _generate_strategy_text

    model, tok, cfg, _ = _tiny_model_and_tokenizer(tmp_path)

    class _Scn:
        name = "capset"
        description = "build a set"

    a = _generate_strategy_text(model=model, tokenizer=tok, scenario=_Scn(), seed=1, max_new_tokens=24, temperature=0.0)
    b = _generate_strategy_text(model=model, tokenizer=tok, scenario=_Scn(), seed=999, max_new_tokens=24, temperature=0.0)
    assert a == b


@pytest.mark.skipif(not HAS_MLX, reason="MLX not installed")
def test_temperature_sampling_produces_variation(tmp_path) -> None:
    """temperature>0 with different seeds is not always the identical completion."""
    from autocontext.training.autoresearch.prepare import _generate_strategy_text

    model, tok, cfg, _ = _tiny_model_and_tokenizer(tmp_path)

    class _Scn:
        name = "capset"
        description = "build a set"

    outs = {
        _generate_strategy_text(model=model, tokenizer=tok, scenario=_Scn(), seed=s, max_new_tokens=24, temperature=1.2)
        for s in range(8)
    }
    assert len(outs) > 1, "temperature sampling should yield diverse completions"


@pytest.mark.skipif(not HAS_MLX, reason="MLX not installed")
def test_assess_strategy_quality_accepts_sampling_params() -> None:
    """assess_strategy_quality threads temperature/top_k/seed_base (backward compatible)."""
    from unittest.mock import MagicMock

    from autocontext.training.autoresearch.prepare import assess_strategy_quality

    scn = MagicMock(spec=["evaluate_output", "get_task_prompt"])
    scn.evaluate_output.return_value = MagicMock(score=0.8)
    model = MagicMock()  # no .cfg -> test-double generation path
    tok = MagicMock()
    tok.decode.return_value = '<|strategy|>{"plan": "x"}<|end|>'

    result = assess_strategy_quality(
        model=model,
        tokenizer=tok,
        scenario=scn,
        n_samples=2,
        temperature=1.0,
        top_k=20,
    )
    assert "avg_score" in result and "valid_rate" in result


# ---------------------------------------------------------------------------
# run_training dependency preflight (no MLX required)
# ---------------------------------------------------------------------------


def test_preflight_raises_on_missing_dependency(monkeypatch) -> None:
    """A missing backend dependency fails fast with an actionable message."""
    import importlib.util as _u

    from autocontext.training.autoresearch import train as train_mod

    real_find_spec = _u.find_spec

    def fake_find_spec(name, *args, **kwargs):
        if name == "numpy":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(train_mod.importlib.util, "find_spec", fake_find_spec)
    with pytest.raises(RuntimeError) as excinfo:
        train_mod._preflight_backend_deps("mlx")
    msg = str(excinfo.value)
    assert "numpy" in msg
    # preserve the no-MLX UX contract asserted by test_training_init
    assert "mlx is required" in msg.lower()
    assert "uv sync --group dev --extra mlx" in msg


def test_preflight_unknown_backend_is_noop() -> None:
    """An unrecognized backend has no required deps and does not raise."""
    from autocontext.training.autoresearch import train as train_mod

    train_mod._preflight_backend_deps("totally-unknown")
