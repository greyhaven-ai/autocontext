"""AC-708 slice 2b: MLX-trained logistic-regression advisor tests.

The schema-acceptance tests (CLI mutex, clear error when MLX is not
installed, checkpoint loader accepting the new kind) run on every
platform. Tests that actually exercise the MLX training path are
gated on :data:`HAS_MLX_ADVISOR` so CI without the optional MLX
extra still runs the rest of the suite cleanly.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.hermes.advisor import (
    CuratorDecisionExample,
    evaluate,
    train_baseline,
)
from autocontext.hermes.mlx_trained_advisor import (
    HAS_MLX_ADVISOR,
    MLX_CHECKPOINT_KIND,
    save_mlx_advisor,
    train_mlx_logistic,
)
from autocontext.hermes.trained_advisor import LogisticRegressionAdvisor, load_advisor

# --- Fixtures (shared with slice 2a; reproduced here to keep this test
#     file self-contained and skippable on non-MLX hosts) ----------------


def _example(*, name: str, label: str, use_count: int = 0) -> CuratorDecisionExample:
    return CuratorDecisionExample(
        skill_name=name,
        label=label,
        state="active",
        provenance="agent-created",
        pinned=False,
        use_count=use_count,
        view_count=0,
        patch_count=0,
    )


def _separable_dataset() -> list[CuratorDecisionExample]:
    """High-use skills are consolidated; cold skills are pruned."""
    examples: list[CuratorDecisionExample] = []
    for i in range(20):
        examples.append(_example(name=f"hot{i}", label="consolidated", use_count=10 + i))
    for i in range(20):
        examples.append(_example(name=f"cold{i}", label="pruned", use_count=0))
    return examples


# --- Schema acceptance tests (run on every platform) ----------------------


def test_require_mlx_message_is_actionable() -> None:
    """The CLI surface raises a clear message when --mlx is requested
    without the optional MLX extra installed. This unit test checks
    the underlying _require_mlx helper directly so the assertion does
    not depend on CliRunner exit-code propagation."""
    from autocontext.hermes import mlx_trained_advisor as _mod

    if HAS_MLX_ADVISOR:
        # Cannot exercise the raise path here; the CLI-mutex test below
        # covers the inverse case.
        return
    with pytest.raises(RuntimeError, match=r"MLX is not installed.*\[mlx\]"):
        _mod._require_mlx()


def test_cli_train_advisor_three_way_mutex_requires_one_backend(tmp_path: Path) -> None:
    """AC-708 slice 2b: the slice extended the slice-1/2a two-flag mutex
    to a three-way (--baseline / --logistic / --mlx). Slice 2c extended
    it again to four-way; this test pins the "at least one backend must
    be passed" invariant, accepting either wording so the test stays
    stable as future slices add CUDA / future backends."""
    src = tmp_path / "data.jsonl"
    src.write_text(
        json.dumps(
            {
                "example_id": "r:s:consolidated",
                "task_kind": "curator-decisions",
                "source": {
                    "curator_run_path": "/tmp/r.json",
                    "started_at": "2026-05-10T00:00:00Z",
                },
                "input": {
                    "skill_name": "s",
                    "skill_state": "active",
                    "skill_provenance": "agent-created",
                    "skill_pinned": False,
                    "skill_use_count": 1,
                    "skill_view_count": 0,
                    "skill_patch_count": 0,
                    "skill_activity_count": 1,
                    "skill_last_activity_at": None,
                },
                "label": "consolidated",
                "confidence": "strong",
                "redactions": [],
                "context": {"run_provider": "anthropic", "run_model": "x", "run_counts": {}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    # No flag set -> require exactly one.
    result = CliRunner().invoke(app, ["hermes", "train-advisor", "--data", str(src), "--json"])
    assert result.exit_code != 0
    assert "exactly one of --baseline, --logistic" in result.output
    assert "--mlx" in result.output


def test_cli_train_advisor_baseline_and_logistic_are_mutex(tmp_path: Path) -> None:
    """Two backends at once is also an error."""
    src = tmp_path / "data.jsonl"
    src.write_text(
        json.dumps(
            {
                "example_id": "r:s:consolidated",
                "task_kind": "curator-decisions",
                "source": {
                    "curator_run_path": "/tmp/r.json",
                    "started_at": "2026-05-10T00:00:00Z",
                },
                "input": {
                    "skill_name": "s",
                    "skill_state": "active",
                    "skill_provenance": "agent-created",
                    "skill_pinned": False,
                    "skill_use_count": 1,
                    "skill_view_count": 0,
                    "skill_patch_count": 0,
                    "skill_activity_count": 1,
                    "skill_last_activity_at": None,
                },
                "label": "consolidated",
                "confidence": "strong",
                "redactions": [],
                "context": {"run_provider": "anthropic", "run_model": "x", "run_counts": {}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        app,
        ["hermes", "train-advisor", "--data", str(src), "--baseline", "--logistic", "--json"],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_cli_train_advisor_mlx_without_mlx_installed_emits_clear_error(tmp_path: Path) -> None:
    """When --mlx is passed but the optional extra is not installed,
    the CLI must surface a clear actionable message that names the
    extra to install, rather than crashing inside an opaque ImportError."""
    if HAS_MLX_ADVISOR:
        pytest.skip("MLX is installed; this test exercises the not-installed path")
    src = tmp_path / "data.jsonl"
    src.write_text(
        json.dumps(
            {
                "example_id": "r:s:consolidated",
                "task_kind": "curator-decisions",
                "source": {
                    "curator_run_path": "/tmp/r.json",
                    "started_at": "2026-05-10T00:00:00Z",
                },
                "input": {
                    "skill_name": "s",
                    "skill_state": "active",
                    "skill_provenance": "agent-created",
                    "skill_pinned": False,
                    "skill_use_count": 1,
                    "skill_view_count": 0,
                    "skill_patch_count": 0,
                    "skill_activity_count": 1,
                    "skill_last_activity_at": None,
                },
                "label": "consolidated",
                "confidence": "strong",
                "redactions": [],
                "context": {"run_provider": "anthropic", "run_model": "x", "run_counts": {}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["hermes", "train-advisor", "--data", str(src), "--mlx", "--json"])
    assert result.exit_code != 0
    assert "MLX is not installed" in result.output
    assert "[mlx]" in result.output


# --- MLX-gated training + round-trip tests --------------------------------


@pytest.mark.skipif(not HAS_MLX_ADVISOR, reason="MLX not installed")
def test_train_mlx_logistic_returns_a_LogisticRegressionAdvisor() -> None:
    """Slice 2b returns the same dataclass as slice 2a so inference
    consumers (recommend --advisor) do not need to dispatch on backend."""
    examples = _separable_dataset()
    trained = train_mlx_logistic(examples, epochs=50, seed=0)
    assert isinstance(trained, LogisticRegressionAdvisor)
    assert trained.labels == ("consolidated", "pruned")
    assert len(trained.weights) == 2
    assert all(len(row) == len(trained.feature_names) for row in trained.weights)


@pytest.mark.skipif(not HAS_MLX_ADVISOR, reason="MLX not installed")
def test_mlx_trained_advisor_beats_baseline_on_separable_data() -> None:
    examples = _separable_dataset()
    baseline = train_baseline(examples)
    mlx_advisor = train_mlx_logistic(examples, epochs=100, seed=0)
    baseline_metrics = evaluate(baseline, examples)
    mlx_metrics = evaluate(mlx_advisor, examples)
    assert mlx_metrics.accuracy > baseline_metrics.accuracy


@pytest.mark.skipif(not HAS_MLX_ADVISOR, reason="MLX not installed")
def test_save_load_mlx_advisor_round_trip(tmp_path: Path) -> None:
    """save_mlx_advisor writes a checkpoint with kind="mlx_logistic_regression";
    load_advisor accepts it and reconstructs the same advisor (predictions
    are identical because predict math is backend-agnostic)."""
    examples = _separable_dataset()
    trained = train_mlx_logistic(examples, epochs=50, seed=0)
    path = tmp_path / "mlx-advisor.json"
    save_mlx_advisor(trained, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["kind"] == MLX_CHECKPOINT_KIND
    assert payload["backend"] == "mlx"
    loaded = load_advisor(path)
    assert isinstance(loaded, LogisticRegressionAdvisor)
    # Sanity: round-tripped advisor predicts the same labels as the
    # original on the training set (we already verified beats-baseline
    # above; this just confirms serialization is lossless for predictions).
    for ex in examples:
        assert trained.predict(ex.features) == loaded.predict(ex.features)


@pytest.mark.skipif(not HAS_MLX_ADVISOR, reason="MLX not installed")
def test_train_mlx_logistic_raises_on_empty_dataset() -> None:
    with pytest.raises(ValueError, match="no labeled examples"):
        train_mlx_logistic([])


@pytest.mark.skipif(not HAS_MLX_ADVISOR, reason="MLX not installed")
def test_cli_train_advisor_mlx_writes_checkpoint(tmp_path: Path) -> None:
    """End-to-end CLI: --mlx --checkpoint writes a checkpoint that
    load_advisor accepts."""

    def _row(name: str, label: str, *, use_count: int = 0) -> dict:
        return {
            "example_id": f"r:{name}:{label}",
            "task_kind": "curator-decisions",
            "source": {
                "curator_run_path": "/tmp/r.json",
                "started_at": "2026-05-10T00:00:00Z",
            },
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

    src = tmp_path / "data.jsonl"
    with src.open("w", encoding="utf-8") as fh:
        for i in range(20):
            fh.write(json.dumps(_row(f"hot{i}", "consolidated", use_count=10)) + "\n")
        for i in range(20):
            fh.write(json.dumps(_row(f"cold{i}", "pruned", use_count=0)) + "\n")
    checkpoint = tmp_path / "mlx-advisor.json"
    metrics = tmp_path / "metrics.json"
    result = CliRunner().invoke(
        app,
        [
            "hermes",
            "train-advisor",
            "--data",
            str(src),
            "--mlx",
            "--output",
            str(metrics),
            "--checkpoint",
            str(checkpoint),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["advisor_kind"] == "mlx_logistic_regression"
    assert payload["backend"] == "mlx"
    assert checkpoint.exists()
    loaded = load_advisor(checkpoint)
    assert isinstance(loaded, LogisticRegressionAdvisor)


_ = sqlite3  # keep imported for parity with the slice-2a recommend test; not used here
