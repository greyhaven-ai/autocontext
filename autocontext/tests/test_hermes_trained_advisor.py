"""AC-708 slice 2a: pure-Python logistic-regression curator advisor.

DDD/TDD coverage for the trained-backend contract that lives on top
of slice 1's data + baseline:

* :class:`LogisticRegressionAdvisor` is a frozen value type carrying
  learned weights, intercepts, label order, and the feature encoder.
  Implements the existing :class:`Advisor` Protocol so the AC-708
  slice 1 :func:`evaluate` and the AC-709 :func:`recommend` work
  unchanged.
* :func:`train_logistic` produces a calibrated multinomial classifier
  via softmax + cross-entropy gradient descent over a fixed feature
  encoding (one-hot ``state``, one-hot ``provenance``, ``pinned``,
  log1p of ``use_count`` / ``view_count`` / ``patch_count``).
* :meth:`LogisticRegressionAdvisor.predict_proba` returns a per-label
  probability dict that sums to 1.
* :func:`save_advisor` + :func:`load_advisor` round-trip a checkpoint
  through JSON without changing predictions.
* Trained advisor beats the always-majority baseline on a contrived
  dataset where features actually predict the label.
* CLI `autoctx hermes train-advisor --logistic --output ckpt.json`
  writes a usable checkpoint; `autoctx hermes recommend --advisor
  <ckpt>` loads and uses it (closes the AC-705 → AC-708 → AC-709
  loop end-to-end with a real trained advisor).

The dataset stays small (< 100 examples) so pure-Python gradient
descent is plenty. MLX and CUDA backends are slice 2b/2c follow-ups
behind the same :class:`Advisor` Protocol.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autocontext.hermes.advisor import (
    AdvisorMetrics,
    CuratorDecisionExample,
    SkillFeatures,
    evaluate,
    train_baseline,
)
from autocontext.hermes.trained_advisor import (
    _FEATURE_NAMES,  # noqa: PLC2701 - canonical feature list for checkpoint fixture
    LogisticRegressionAdvisor,
    load_advisor,
    save_advisor,
    train_logistic,
)


def _example(
    *,
    name: str,
    label: str,
    state: str = "active",
    provenance: str = "agent-created",
    pinned: bool = False,
    use_count: int = 0,
    view_count: int = 0,
    patch_count: int = 0,
) -> CuratorDecisionExample:
    return CuratorDecisionExample(
        skill_name=name,
        label=label,
        state=state,
        provenance=provenance,
        pinned=pinned,
        use_count=use_count,
        view_count=view_count,
        patch_count=patch_count,
    )


def _separable_dataset() -> list[CuratorDecisionExample]:
    """Hand-tuned dataset where features actually predict the label.

    Rule of thumb the trained advisor must learn:
    * use_count >= 5 → ``consolidated`` (frequently used skills get merged)
    * use_count == 0 → ``pruned`` (unused skills get pruned)
    """
    examples: list[CuratorDecisionExample] = []
    for i in range(30):
        examples.append(_example(name=f"hot{i}", label="consolidated", use_count=10 + i))
    for i in range(30):
        examples.append(_example(name=f"cold{i}", label="pruned", use_count=0))
    return examples


# --- LogisticRegressionAdvisor + train_logistic ---------------------------


def test_train_logistic_produces_advisor_with_learned_weights() -> None:
    advisor = train_logistic(_separable_dataset())
    assert isinstance(advisor, LogisticRegressionAdvisor)
    # Labels are recorded in CANONICAL_LABELS order so save/load is stable.
    assert "consolidated" in advisor.labels
    assert "pruned" in advisor.labels
    # Weight matrix has one row per label, one column per feature.
    assert len(advisor.weights) == len(advisor.labels)
    assert all(len(row) == len(advisor.feature_names) for row in advisor.weights)


def test_trained_advisor_predict_is_callable_via_advisor_protocol() -> None:
    advisor = train_logistic(_separable_dataset())
    feats = SkillFeatures(
        skill_name="x",
        state="active",
        provenance="agent-created",
        pinned=False,
        use_count=20,
        view_count=0,
        patch_count=0,
    )
    assert advisor.predict(feats) == "consolidated"

    cold = SkillFeatures(
        skill_name="y",
        state="active",
        provenance="agent-created",
        pinned=False,
        use_count=0,
        view_count=0,
        patch_count=0,
    )
    assert advisor.predict(cold) == "pruned"


def test_predict_proba_sums_to_one() -> None:
    advisor = train_logistic(_separable_dataset())
    feats = SkillFeatures(
        skill_name="x",
        state="active",
        provenance="agent-created",
        pinned=False,
        use_count=20,
        view_count=0,
        patch_count=0,
    )
    probs = advisor.predict_proba(feats)
    assert set(probs.keys()) == set(advisor.labels)
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-6)
    assert all(0.0 <= p <= 1.0 for p in probs.values())


def test_trained_advisor_beats_baseline_on_separable_data() -> None:
    """The trained advisor must out-perform the majority-class baseline
    on a dataset where features carry signal. If it doesn't, training
    is broken or the loss isn't decreasing."""
    examples = _separable_dataset()
    baseline = train_baseline(examples)
    trained = train_logistic(examples)
    baseline_metrics: AdvisorMetrics = evaluate(baseline, examples)
    trained_metrics: AdvisorMetrics = evaluate(trained, examples)
    assert trained_metrics.accuracy > baseline_metrics.accuracy
    # On this clean dataset the trained advisor should reach >= 95%.
    assert trained_metrics.accuracy >= 0.95


def test_trained_advisor_is_deterministic_for_same_seed() -> None:
    """Two training runs over the same examples with the same seed
    produce the same weights (no hidden randomness in the gradient
    descent path)."""
    examples = _separable_dataset()
    a = train_logistic(examples, seed=42)
    b = train_logistic(examples, seed=42)
    assert a.weights == b.weights
    assert a.intercepts == b.intercepts


def test_train_logistic_raises_on_empty_dataset() -> None:
    with pytest.raises(ValueError, match="no labeled examples"):
        train_logistic([])


def test_train_logistic_handles_single_label_dataset() -> None:
    """When every example shares the same label, the advisor still
    trains (collapses to a single-class predictor) without raising."""
    examples = [_example(name=f"s{i}", label="consolidated", use_count=i) for i in range(10)]
    advisor = train_logistic(examples)
    feats = SkillFeatures(
        skill_name="x",
        state="active",
        provenance="agent-created",
        pinned=False,
        use_count=5,
        view_count=0,
        patch_count=0,
    )
    assert advisor.predict(feats) == "consolidated"


# --- Checkpoint round-trip ------------------------------------------------


def test_save_load_round_trip_preserves_predictions(tmp_path: Path) -> None:
    examples = _separable_dataset()
    advisor = train_logistic(examples)
    path = tmp_path / "advisor.json"
    save_advisor(advisor, path)
    loaded = load_advisor(path)
    assert isinstance(loaded, LogisticRegressionAdvisor)
    for ex in examples:
        assert advisor.predict(ex.features) == loaded.predict(ex.features)


def test_checkpoint_has_stable_schema(tmp_path: Path) -> None:
    """Checkpoints are JSON-friendly with a stable schema so tools
    outside autocontext can inspect them (and so a checkpoint stays
    readable across minor refactors)."""
    advisor = train_logistic(_separable_dataset())
    path = tmp_path / "advisor.json"
    save_advisor(advisor, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["kind"] == "logistic_regression"
    assert "version" in payload
    assert "labels" in payload
    assert "feature_names" in payload
    assert "weights" in payload
    assert "intercepts" in payload


def test_load_advisor_rejects_unknown_kind(tmp_path: Path) -> None:
    """A checkpoint with a `kind` outside the accepted set must raise.
    Accepted kinds today: `logistic_regression` (slice 2a),
    `mlx_logistic_regression` (slice 2b), `cuda_logistic_regression`
    (slice 2c, reserved). Anything else fails fast."""
    path = tmp_path / "advisor.json"
    path.write_text(
        json.dumps(
            {
                "kind": "mlx_neural_net",  # not in accepted set
                "version": 1,
                "labels": [],
                "feature_names": [],
                "weights": [],
                "intercepts": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown advisor kind"):
        load_advisor(path)


def test_load_advisor_accepts_mlx_kind(tmp_path: Path) -> None:
    """AC-708 slice 2b: a checkpoint with `kind: "mlx_logistic_regression"`
    must load as a regular :class:`LogisticRegressionAdvisor`. Inference
    math is identical to slice 2a; only the training backend differs, so
    the loaded type is uniform across backends and `recommend --advisor`
    works against either checkpoint."""
    path = tmp_path / "mlx-advisor.json"
    path.write_text(
        json.dumps(
            {
                "kind": "mlx_logistic_regression",
                "version": 1,
                "labels": ["consolidated", "pruned"],
                "feature_names": list(_FEATURE_NAMES),
                "weights": [[0.0] * len(_FEATURE_NAMES), [0.0] * len(_FEATURE_NAMES)],
                "intercepts": [0.0, 0.0],
                "trained_on": 10,
                "seed": 0,
                "epochs": 50,
                "learning_rate": 0.5,
                "backend": "mlx",
            }
        ),
        encoding="utf-8",
    )
    loaded = load_advisor(path)
    assert isinstance(loaded, LogisticRegressionAdvisor)
    assert loaded.labels == ("consolidated", "pruned")


def test_load_advisor_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_advisor(tmp_path / "does-not-exist.json")


def test_load_advisor_rejects_corrupt_json(tmp_path: Path) -> None:
    path = tmp_path / "advisor.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid advisor checkpoint"):
        load_advisor(path)


def test_load_advisor_rejects_class_dimension_mismatch(tmp_path: Path) -> None:
    """PR #980 review (P2): a checkpoint where ``labels`` /
    ``weights`` / ``intercepts`` disagree on the number of classes
    must fail at load time, not inside ``predict_proba`` with a
    confusing ``zip`` traceback far from the malformed file."""
    path = tmp_path / "advisor.json"
    path.write_text(
        json.dumps(
            {
                "kind": "logistic_regression",
                "version": 1,
                "labels": ["consolidated", "pruned"],
                "feature_names": ["a", "b"],
                "weights": [[0.0, 0.0]],
                "intercepts": [0.0, 0.0],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid advisor checkpoint"):
        load_advisor(path)


def test_load_advisor_rejects_feature_dimension_mismatch(tmp_path: Path) -> None:
    """PR #980 review (P2): a weights row whose length disagrees with
    ``feature_names`` is schema-invalid and must fail at load time."""
    path = tmp_path / "advisor.json"
    path.write_text(
        json.dumps(
            {
                "kind": "logistic_regression",
                "version": 1,
                "labels": ["consolidated", "pruned"],
                "feature_names": ["a", "b", "c"],
                "weights": [[0.0, 0.0], [0.0, 0.0]],
                "intercepts": [0.0, 0.0],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid advisor checkpoint"):
        load_advisor(path)


# --- CLI integration ------------------------------------------------------


def _ac705_row(name: str, label: str, *, use_count: int = 0) -> dict:
    return {
        "example_id": f"r:{name}:{label}",
        "task_kind": "curator-decisions",
        "source": {"curator_run_path": "/tmp/r.json", "started_at": "2026-05-10T00:00:00Z"},
        "input": {
            "skill_name": name,
            "skill_state": "active",
            "skill_provenance": "agent-created",
            "skill_pinned": False,
            "skill_use_count": use_count,
            "skill_view_count": 0,
            "skill_patch_count": 0,
            "skill_activity_count": use_count,
            "skill_last_activity_at": None,
        },
        "label": label,
        "confidence": "strong",
        "redactions": [],
        "context": {"run_provider": "anthropic", "run_model": "x", "run_counts": {}},
    }


def test_cli_train_advisor_logistic_writes_checkpoint(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from autocontext.cli import app

    src = tmp_path / "data.jsonl"
    with src.open("w", encoding="utf-8") as fh:
        for i in range(20):
            fh.write(json.dumps(_ac705_row(f"hot{i}", "consolidated", use_count=10)) + "\n")
        for i in range(20):
            fh.write(json.dumps(_ac705_row(f"cold{i}", "pruned", use_count=0)) + "\n")
    checkpoint = tmp_path / "advisor.json"
    metrics = tmp_path / "metrics.json"

    result = CliRunner().invoke(
        app,
        [
            "hermes",
            "train-advisor",
            "--data",
            str(src),
            "--logistic",
            "--output",
            str(metrics),
            "--checkpoint",
            str(checkpoint),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["advisor_kind"] == "logistic_regression"
    assert checkpoint.exists()
    loaded = load_advisor(checkpoint)
    assert isinstance(loaded, LogisticRegressionAdvisor)


def test_cli_recommend_consumes_trained_checkpoint(tmp_path: Path) -> None:
    """End-to-end: train a logistic advisor → emit recommendations
    against a live Hermes home using the trained checkpoint."""
    import sqlite3 as _sqlite

    from typer.testing import CliRunner

    from autocontext.cli import app

    # Train and persist a logistic advisor.
    src = tmp_path / "data.jsonl"
    with src.open("w", encoding="utf-8") as fh:
        for i in range(20):
            fh.write(json.dumps(_ac705_row(f"hot{i}", "consolidated", use_count=10)) + "\n")
        for i in range(20):
            fh.write(json.dumps(_ac705_row(f"cold{i}", "pruned", use_count=0)) + "\n")
    checkpoint = tmp_path / "advisor.json"
    metrics = tmp_path / "metrics.json"

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "hermes",
            "train-advisor",
            "--data",
            str(src),
            "--logistic",
            "--output",
            str(metrics),
            "--checkpoint",
            str(checkpoint),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output

    # Stand up a minimal Hermes home with one active hot skill.
    home = tmp_path / "hermes"
    skills_dir = home / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "hot-skill").mkdir()
    (skills_dir / "hot-skill" / "SKILL.md").write_text(
        "---\nname: hot-skill\ndescription: test\n---\n# hot-skill\n",
        encoding="utf-8",
    )
    (skills_dir / ".usage.json").write_text(
        json.dumps({"hot-skill": {"state": "active", "pinned": False, "use_count": 25, "view_count": 0, "patch_count": 0}}),
        encoding="utf-8",
    )
    _ = _sqlite  # imported to keep parity with other Hermes tests; not used here

    recs_out = tmp_path / "recs.jsonl"
    result2 = runner.invoke(
        app,
        [
            "hermes",
            "recommend",
            "--home",
            str(home),
            "--advisor",
            str(checkpoint),
            "--output",
            str(recs_out),
            "--json",
        ],
    )
    assert result2.exit_code == 0, result2.output
    payload = json.loads(result2.output)
    assert payload["advisor_kind"] == "logistic_regression"
    rows = [json.loads(line) for line in recs_out.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    # The trained advisor on a hot skill predicts "consolidated".
    assert rows[0]["predicted_action"] == "consolidated"


def test_cli_train_advisor_requires_one_of_baseline_or_logistic(tmp_path: Path) -> None:
    """Passing neither --baseline nor --logistic must fail loudly so
    the operator picks a backend deliberately."""
    from typer.testing import CliRunner

    from autocontext.cli import app

    src = tmp_path / "data.jsonl"
    src.write_text(json.dumps(_ac705_row("s", "consolidated", use_count=1)) + "\n", encoding="utf-8")
    out = tmp_path / "metrics.json"
    result = CliRunner().invoke(
        app,
        [
            "hermes",
            "train-advisor",
            "--data",
            str(src),
            "--output",
            str(out),
            "--json",
        ],
    )
    assert result.exit_code != 0


def test_cli_train_advisor_rejects_checkpoint_same_as_data(tmp_path: Path) -> None:
    """PR #980 review (P2): pointing --checkpoint at the same file as
    --data would clobber the source dataset mid-training. Refuse
    before touching disk."""
    from typer.testing import CliRunner

    from autocontext.cli import app

    src = tmp_path / "shared.jsonl"
    src.write_text(json.dumps(_ac705_row("s", "consolidated", use_count=1)) + "\n", encoding="utf-8")
    out = tmp_path / "metrics.json"
    result = CliRunner().invoke(
        app,
        [
            "hermes",
            "train-advisor",
            "--data",
            str(src),
            "--logistic",
            "--output",
            str(out),
            "--checkpoint",
            str(src),
            "--json",
        ],
    )
    assert result.exit_code != 0
    assert "refusing to overwrite the source dataset" in result.output
    # The dataset must be untouched (still valid JSONL with one row).
    rows = [line for line in src.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1


def test_cli_train_advisor_rejects_checkpoint_same_as_output(tmp_path: Path) -> None:
    """PR #980 review (P2): pointing --checkpoint at the same file as
    --output would clobber the JSON metrics mid-write. Refuse before
    touching disk."""
    from typer.testing import CliRunner

    from autocontext.cli import app

    src = tmp_path / "data.jsonl"
    src.write_text(json.dumps(_ac705_row("s", "consolidated", use_count=1)) + "\n", encoding="utf-8")
    shared = tmp_path / "shared.json"
    # Pre-create the shared file so samefile() can resolve both args.
    shared.write_text("{}", encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "hermes",
            "train-advisor",
            "--data",
            str(src),
            "--logistic",
            "--output",
            str(shared),
            "--checkpoint",
            str(shared),
            "--json",
        ],
    )
    assert result.exit_code != 0
    assert "refusing to overwrite the metrics output" in result.output
