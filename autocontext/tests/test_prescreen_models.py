from __future__ import annotations

import random

import pytest

pytest.importorskip("sklearn")

from autocontext.prescreen.calibrate import inner_skip_threshold, skip_threshold  # noqa: E402
from autocontext.prescreen.dataset import Example  # noqa: E402
from autocontext.prescreen.models import MODEL_FACTORIES, build_model, log_loss  # noqa: E402


def synthetic(n: int, seed: int = 0) -> list[Example]:
    """Low previous score and the word 'stub' mean the judge fails the round; 10% label noise."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        previous = rng.random()
        fails = previous < 0.6
        if rng.random() < 0.1:
            fails = not fails
        word = "stub placeholder" if previous < 0.6 else "complete answer"
        out.append(
            Example(
                row_id=i,
                loop_id=f"L{i}",
                group=f"g{i % 12}",
                created_at=f"t{i:05d}",
                round_number=2,
                failed=fails,
                structural=(2.0, previous, previous, previous, 5.0, 1.0, 0.5, 1.0),
                text=f"task prompt shared words {word} number {i % 7}",
            )
        )
    return out


def test_skip_threshold_picks_the_lowest_qualifying_cut() -> None:
    p = [0.99] * 20 + [0.9] * 10 + [0.5] * 10
    failed = [True] * 20 + [True] * 9 + [False] + [False] * 10
    assert skip_threshold(p, failed) == 0.9  # 29/30 = 0.967 >= 0.95 at the 0.9 cut
    assert skip_threshold(p, failed, target=0.99) == 0.99
    assert skip_threshold(p[:10], failed[:10]) is None  # fewer than 20 covered rounds
    assert skip_threshold([], []) is None


def test_skip_threshold_never_splits_ties() -> None:
    p = [0.8] * 25
    failed = [True] * 24 + [False]
    assert skip_threshold(p, failed) == 0.8
    assert skip_threshold(p, [True] * 23 + [False] * 2) is None


def test_inner_threshold_needs_two_classes_in_the_head() -> None:
    same = [Example(i, "L", "g", f"t{i}", 2, True, (0.0,) * 8, "x y") for i in range(50)]
    assert inner_skip_threshold(lambda: build_model("p0"), same) is None


@pytest.mark.parametrize("key", sorted(MODEL_FACTORIES))
def test_every_model_fits_and_predicts_probabilities(key: str) -> None:
    data = synthetic(240)
    model = build_model(key)
    model.fit(data[:180])
    p = model.predict_fail(data[180:])
    assert len(p) == 60 and all(0.0 <= x <= 1.0 for x in p)


def test_structural_and_hybrid_models_learn_the_signal() -> None:
    data = synthetic(400, seed=1)
    base = build_model("p0")
    base.fit(data[:300])
    for key in ("p1", "p3"):
        model = build_model(key)
        model.fit(data[:300])
        truth = [e.failed for e in data[300:]]
        assert log_loss(model.predict_fail(data[300:]), truth) < log_loss(base.predict_fail(data[300:]), truth) - 0.1


def test_single_class_training_falls_back_to_the_base_rate() -> None:
    data = [Example(i, "L", "g", f"t{i}", 2, False, (0.5,) * 8, "a b a b") for i in range(30)]
    for key in ("p1", "p2", "p3"):
        model = build_model(key)
        model.fit(data)
        assert model.predict_fail(data[:3]) == [0.0, 0.0, 0.0]


def test_unknown_model_key() -> None:
    with pytest.raises(ValueError, match="unknown pre-screen model"):
        build_model("p9")


def test_the_cli_validates_model_keys_against_the_ladder() -> None:
    from autocontext.cli_prescreen import MODEL_KEYS

    assert MODEL_KEYS == tuple(MODEL_FACTORIES)


def test_log_loss_is_finite_for_confident_misses() -> None:
    assert log_loss([1.0], [False]) < 10
