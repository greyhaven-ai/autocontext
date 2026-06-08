"""On-policy distillation: per-token reverse-KL loss kernel (Thinking Machines recipe).

The student samples on-policy; the dense signal is the per-token reverse KL
KL(student || teacher) over the student-generated positions. These pin the loss kernel
with hand-computed values and the frozen-teacher gradient contract (pure mlx, no model).
"""

from __future__ import annotations

import math

import pytest

# importorskip MUST run before any mlx import, so CI (no MLX) skips at collection rather
# than erroring. Bind the mlx modules through it instead of a top-level `import mlx.*`.
mx = pytest.importorskip("mlx.core")
nn = pytest.importorskip("mlx.nn")
optim = pytest.importorskip("mlx.optimizers")

from autocontext.training.autoresearch.on_policy_distill import (  # noqa: E402
    assert_vocab_compatible,
    distill_loss,
    distill_over_prompts,
    distill_update_step,
    on_policy_distill_step,
    reverse_kl_per_token,
    sample_completion,
)


class _TinyLM(nn.Module):
    """Minimal autoregressive stub: ids [B,T] -> logits [B,T,V]."""

    def __init__(self, vocab: int, dim: int) -> None:
        super().__init__()
        self.embed = nn.Embedding(vocab, dim)
        self.out = nn.Linear(dim, vocab)

    def __call__(self, ids: mx.array) -> mx.array:
        return self.out(self.embed(ids))


class _ConstLM:
    """Callable stub whose next-token argmax is always ``hot`` (no params)."""

    def __init__(self, vocab: int, hot: int) -> None:
        self.vocab = vocab
        self.hot = hot

    def __call__(self, ids: mx.array) -> mx.array:
        b, t = ids.shape
        onehot = (mx.arange(self.vocab) == self.hot).astype(mx.float32) * 10.0
        return mx.broadcast_to(onehot, (b, t, self.vocab))


def test_identical_distributions_have_zero_kl() -> None:
    logits = mx.array([[[0.3, 1.2, -0.5, 2.0]]])  # [B=1, T=1, V=4]
    mask = mx.array([[1.0]])
    kl = reverse_kl_per_token(logits, logits, mask)
    assert abs(float(kl)) < 1e-5


def test_two_class_reverse_kl_matches_hand_computation() -> None:
    # student p = [.5, .5]; teacher p = [.25, .75] (logits [0, ln3]).
    # reverse KL = .5*(ln.5 - ln.25) + .5*(ln.5 - ln.75) = .5*ln2 + .5*ln(2/3) ~= 0.143841
    student = mx.array([[[0.0, 0.0]]])
    teacher = mx.array([[[0.0, math.log(3.0)]]])
    mask = mx.array([[1.0]])
    kl = float(reverse_kl_per_token(student, teacher, mask))
    expected = 0.5 * math.log(2.0) + 0.5 * math.log(2.0 / 3.0)
    assert kl == pytest.approx(expected, abs=1e-5)


def test_mask_excludes_unflagged_positions() -> None:
    # Two positions; position 0 has a KL contribution, position 1 is masked out.
    student = mx.array([[[0.0, 0.0], [0.0, 0.0]]])
    teacher = mx.array([[[0.0, math.log(3.0)], [5.0, -5.0]]])
    mask = mx.array([[1.0, 0.0]])
    kl = float(reverse_kl_per_token(student, teacher, mask))
    expected = 0.5 * math.log(2.0) + 0.5 * math.log(2.0 / 3.0)  # only position 0
    assert kl == pytest.approx(expected, abs=1e-5)


def test_empty_mask_returns_zero_not_nan() -> None:
    student = mx.array([[[0.0, 0.0]]])
    teacher = mx.array([[[1.0, -1.0]]])
    mask = mx.array([[0.0]])
    kl = float(reverse_kl_per_token(student, teacher, mask))
    assert kl == 0.0


def test_high_temperature_flattens_and_shrinks_kl() -> None:
    student = mx.array([[[0.0, 0.0]]])
    teacher = mx.array([[[0.0, math.log(3.0)]]])
    mask = mx.array([[1.0]])
    hot = float(reverse_kl_per_token(student, teacher, mask, temperature=1000.0))
    assert abs(hot) < 1e-3


def test_teacher_is_frozen_gradient_flows_only_to_student() -> None:
    student = mx.array([[[0.2, -0.4, 1.1]]])
    teacher = mx.array([[[0.5, 0.1, -0.2]]])
    mask = mx.array([[1.0]])

    grad_student = mx.grad(lambda s: reverse_kl_per_token(s, teacher, mask))(student)
    grad_teacher = mx.grad(lambda t: reverse_kl_per_token(student, t, mask))(teacher)

    assert float(mx.sum(mx.abs(grad_student))) > 1e-6  # student learns
    assert float(mx.sum(mx.abs(grad_teacher))) < 1e-9  # teacher frozen (stop-gradient)


# ---------------------------------------------------------------------------
# On-policy rollout + distillation step
# ---------------------------------------------------------------------------


def test_sample_completion_shapes_mask_and_greedy_determinism() -> None:
    prompt = mx.array([[1, 3]])  # [B=1, P=2]
    full, mask = sample_completion(_ConstLM(vocab=4, hot=2), prompt, max_tokens=3, temperature=0.0)

    assert full.shape == (1, 5)  # P + max_tokens
    assert [int(x) for x in full[0]] == [1, 3, 2, 2, 2]  # prompt preserved; greedy -> hot token
    # distilled positions are those PREDICTING a generated token: indices P-1 .. P+G-2
    assert [float(x) for x in mask[0]] == [0.0, 1.0, 1.0, 1.0, 0.0]


def test_distill_loss_zero_when_student_equals_teacher() -> None:
    mx.random.seed(0)
    model = _TinyLM(vocab=8, dim=4)
    ids = mx.array([[1, 2, 3, 4]])
    mask = mx.array([[0.0, 1.0, 1.0, 0.0]])
    loss = float(distill_loss(model, model, ids, mask))
    assert abs(loss) < 1e-5


def test_distill_loss_positive_for_different_models_with_student_gradient() -> None:
    mx.random.seed(1)
    student = _TinyLM(vocab=8, dim=4)
    mx.random.seed(2)
    teacher = _TinyLM(vocab=8, dim=4)
    ids = mx.array([[1, 2, 3, 4]])
    mask = mx.array([[0.0, 1.0, 1.0, 1.0]])

    loss = float(distill_loss(student, teacher, ids, mask))
    assert loss > 0.0 and math.isfinite(loss)

    lvg = nn.value_and_grad(student, lambda m: distill_loss(m, teacher, ids, mask))
    _, grads = lvg(student)
    # grad is nonzero somewhere in the student parameter tree
    total = sum(float(mx.sum(mx.abs(g))) for _, g in nn.utils.tree_flatten(grads))
    assert total > 1e-6


def test_distill_update_step_reduces_loss_on_fixed_batch() -> None:
    mx.random.seed(3)
    student = _TinyLM(vocab=8, dim=4)
    mx.random.seed(4)
    teacher = _TinyLM(vocab=8, dim=4)
    ids = mx.array([[1, 2, 3, 4, 5]])
    mask = mx.array([[0.0, 1.0, 1.0, 1.0, 1.0]])
    opt = optim.SGD(learning_rate=0.5)

    first = distill_update_step(student, teacher, opt, ids, mask)
    last = first
    for _ in range(30):
        last = distill_update_step(student, teacher, opt, ids, mask)
    assert last < first  # gradient descent on a fixed batch lowers reverse KL


def test_on_policy_distill_step_runs_and_changes_student() -> None:
    mx.random.seed(5)
    student = _TinyLM(vocab=6, dim=4)
    mx.random.seed(6)
    teacher = _TinyLM(vocab=6, dim=4)
    prompt = mx.array([[1, 2]])
    opt = optim.SGD(learning_rate=0.3)

    before = float(mx.sum(mx.abs(student.out.weight)))
    loss = on_policy_distill_step(student, teacher, opt, prompt, max_tokens=3, sample_temperature=0.0)
    after = float(mx.sum(mx.abs(student.out.weight)))

    assert math.isfinite(loss)
    assert before != after  # the step updated the student's parameters


def test_distill_over_prompts_runs_all_iters_and_trains() -> None:
    mx.random.seed(7)
    student = _TinyLM(vocab=6, dim=4)
    mx.random.seed(8)
    teacher = _TinyLM(vocab=6, dim=4)
    prompts = [mx.array([[1, 2]]), mx.array([[3, 4, 5]])]  # different prompt lengths
    opt = optim.SGD(learning_rate=0.2)

    before = float(mx.sum(mx.abs(student.out.weight)))
    out = distill_over_prompts(student, teacher, opt, prompts, iters=6, max_tokens=2, sample_temperature=0.0, kl_temperature=1.0)
    after = float(mx.sum(mx.abs(student.out.weight)))

    assert out["num_steps"] == 6  # cycles through prompts for the full iteration budget
    assert math.isfinite(out["final_loss"]) and math.isfinite(out["mean_loss"])
    assert before != after


def test_run_training_dispatches_opd(monkeypatch, tmp_path) -> None:
    """`run_training(backend="opd")` routes to the on-policy distillation runner, mapping
    the generic base_model -> student_model and train_steps -> iters."""
    import autocontext.training.autoresearch.on_policy_distill as opd
    from autocontext.training.autoresearch.train import run_training

    captured: dict = {}

    def fake_runner(**kw):
        captured.update(kw)
        return {
            "avg_score": 0.4,
            "valid_rate": 1.0,
            "training_seconds": 0.0,
            "peak_memory_mb": 0.0,
            "num_steps": 5.0,
            "num_params_m": 0.0,
            "depth": 0.0,
        }

    monkeypatch.setattr(opd, "run_on_policy_distillation", fake_runner)

    out = run_training(
        scenario_name="grid_ctf",
        data_path=tmp_path / "d.jsonl",
        output_dir=tmp_path / "o",
        time_budget=10,
        memory_limit_mb=1024,
        backend="opd",
        train_steps=5,
        base_model="student-x",
    )

    assert captured["scenario_name"] == "grid_ctf"
    assert captured["student_model"] == "student-x"
    assert captured["iters"] == 5
    assert out["avg_score"] == 0.4


def test_distill_over_prompts_respects_time_budget() -> None:
    """A spent time budget stops the loop early instead of running all iters."""
    mx.random.seed(9)
    student = _TinyLM(vocab=6, dim=4)
    teacher = _TinyLM(vocab=6, dim=4)
    prompts = [mx.array([[1, 2]])]
    opt = optim.SGD(learning_rate=0.1)

    out = distill_over_prompts(student, teacher, opt, prompts, iters=100, max_tokens=2, sample_temperature=0.0, time_budget=0.0)
    assert out["num_steps"] < 100  # budget cut it short rather than running all 100


def test_assert_vocab_compatible_guards_tokenizer_mismatch() -> None:
    assert_vocab_compatible(151936, 151936)  # equal vocab: fine
    with pytest.raises(ValueError, match="tokenizer"):
        assert_vocab_compatible(151936, 32000)  # mismatched vocab -> clear error, not a shape crash


def test_model_logit_vocab_reads_output_dim_not_tokenizer() -> None:
    """The guard must compare the MODEL's logit vocab (what reverse_kl compares), so a
    padded-vocab mismatch is caught up front rather than crashing in the loss."""
    from autocontext.training.autoresearch.on_policy_distill import _model_logit_vocab

    assert _model_logit_vocab(_TinyLM(vocab=151936, dim=4)) == 151936
    # two models with different logit vocab -> the guard rejects the pair
    s, t = _model_logit_vocab(_TinyLM(vocab=151936, dim=4)), _model_logit_vocab(_TinyLM(vocab=152064, dim=4))
    with pytest.raises(ValueError, match="tokenizer"):
        assert_vocab_compatible(s, t)
