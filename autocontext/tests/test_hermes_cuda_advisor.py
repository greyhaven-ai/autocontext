"""AC-708 slice 2c: PyTorch/CUDA-trained logistic-regression advisor tests.

Schema-acceptance tests (CLI mutex extended to 4-way, clear error
when torch is not installed, checkpoint loader accepting the new
kind) run on every platform. Tests that exercise the actual training
path are gated on :data:`HAS_CUDA_ADVISOR` so CI without the
optional ``cuda`` extra still runs the rest of the suite cleanly.
The training path itself uses CPU torch transparently when CUDA is
unavailable, so the gated tests run on any platform where torch is
installed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.hermes.advisor import (
    CuratorDecisionExample,
    evaluate,
    train_baseline,
)
from autocontext.hermes.cuda_trained_advisor import (
    CUDA_CHECKPOINT_KIND,
    HAS_CUDA_ADVISOR,
    save_cuda_advisor,
    train_cuda_logistic,
)
from autocontext.hermes.trained_advisor import LogisticRegressionAdvisor, load_advisor

# --- Fixtures ------------------------------------------------------------


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


# --- Schema acceptance (run on every platform) ---------------------------


def test_require_torch_message_is_actionable() -> None:
    """When torch is not installed, ``_require_torch`` raises a clear
    message naming the ``cuda`` extra. Skipped if torch happens to be
    installed -- the inverse case (CLI mutex with torch present) is
    covered below."""
    from autocontext.hermes import cuda_trained_advisor as _mod

    if HAS_CUDA_ADVISOR:
        return
    with pytest.raises(RuntimeError, match=r"PyTorch is not installed.*\[cuda\]"):
        _mod._require_torch()


def test_cli_train_advisor_four_way_mutex_requires_one_backend(tmp_path: Path) -> None:
    """AC-708 slice 2c: ``--baseline`` / ``--logistic`` / ``--mlx`` /
    ``--cuda`` are mutually exclusive and exactly one must be passed.
    Zero or two surfaces a loud failure rather than silently picking
    a default."""
    src = tmp_path / "data.jsonl"
    src.write_text(json.dumps(_row("s", "consolidated", use_count=1)) + "\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["hermes", "train-advisor", "--data", str(src), "--json"])
    assert result.exit_code != 0
    # The exact wording covers --baseline, --logistic, --mlx, --cuda.
    assert "exactly one of --baseline, --logistic, --mlx, or --cuda" in result.output


def test_cli_train_advisor_logistic_and_cuda_are_mutex(tmp_path: Path) -> None:
    """Two backends at once is an error."""
    src = tmp_path / "data.jsonl"
    src.write_text(json.dumps(_row("s", "consolidated", use_count=1)) + "\n", encoding="utf-8")
    result = CliRunner().invoke(
        app,
        ["hermes", "train-advisor", "--data", str(src), "--logistic", "--cuda", "--json"],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_cli_train_advisor_cuda_without_torch_installed_emits_clear_error(tmp_path: Path) -> None:
    """When ``--cuda`` is passed but torch is not installed, the CLI
    surfaces a clear actionable message naming the ``cuda`` extra
    rather than crashing inside an opaque ImportError."""
    if HAS_CUDA_ADVISOR:
        pytest.skip("torch is installed; this test exercises the not-installed path")
    src = tmp_path / "data.jsonl"
    src.write_text(json.dumps(_row("s", "consolidated", use_count=1)) + "\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["hermes", "train-advisor", "--data", str(src), "--cuda", "--json"])
    assert result.exit_code != 0
    assert "PyTorch is not installed" in result.output
    assert "[cuda]" in result.output


def test_load_advisor_accepts_cuda_kind(tmp_path: Path) -> None:
    """AC-708 slice 2c: a checkpoint with ``kind:
    "cuda_logistic_regression"`` must load as a regular
    :class:`LogisticRegressionAdvisor` since inference math is
    identical across backends -- only the training backend differs."""
    from autocontext.hermes.trained_advisor import _FEATURE_NAMES  # noqa: PLC2701

    path = tmp_path / "cuda-advisor.json"
    path.write_text(
        json.dumps(
            {
                "kind": "cuda_logistic_regression",
                "version": 1,
                "labels": ["consolidated", "pruned"],
                "feature_names": list(_FEATURE_NAMES),
                "weights": [[0.0] * len(_FEATURE_NAMES), [0.0] * len(_FEATURE_NAMES)],
                "intercepts": [0.0, 0.0],
                "trained_on": 10,
                "seed": 0,
                "epochs": 50,
                "learning_rate": 0.5,
                "backend": "cuda",
                "device": "cpu",
            }
        ),
        encoding="utf-8",
    )
    loaded = load_advisor(path)
    assert isinstance(loaded, LogisticRegressionAdvisor)
    assert loaded.labels == ("consolidated", "pruned")


# --- CUDA-gated training + round-trip tests ------------------------------


@pytest.mark.skipif(not HAS_CUDA_ADVISOR, reason="torch not installed")
def test_train_cuda_logistic_returns_advisor_and_device() -> None:
    """PR #996 review (P2): the trained advisor and the device used for
    training are returned as a tuple so the audit `device` field in
    save_cuda_advisor reflects where training actually ran."""
    examples = _separable_dataset()
    trained, device = train_cuda_logistic(examples, epochs=50, seed=0)
    assert isinstance(trained, LogisticRegressionAdvisor)
    assert trained.labels == ("consolidated", "pruned")
    assert len(trained.weights) == 2
    assert all(len(row) == len(trained.feature_names) for row in trained.weights)
    assert device in {"cuda", "cpu"}


@pytest.mark.skipif(not HAS_CUDA_ADVISOR, reason="torch not installed")
def test_cuda_trained_advisor_beats_baseline_on_separable_data() -> None:
    examples = _separable_dataset()
    baseline = train_baseline(examples)
    cuda_advisor, _ = train_cuda_logistic(examples, epochs=100, seed=0)
    baseline_metrics = evaluate(baseline, examples)
    cuda_metrics = evaluate(cuda_advisor, examples)
    assert cuda_metrics.accuracy > baseline_metrics.accuracy


@pytest.mark.skipif(not HAS_CUDA_ADVISOR, reason="torch not installed")
def test_save_load_cuda_advisor_round_trip(tmp_path: Path) -> None:
    examples = _separable_dataset()
    trained, device = train_cuda_logistic(examples, epochs=50, seed=0)
    path = tmp_path / "cuda-advisor.json"
    save_cuda_advisor(trained, path, device=device)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["kind"] == CUDA_CHECKPOINT_KIND
    assert payload["backend"] == "cuda"
    assert payload["device"] == device  # reflects training device, not save-host device
    loaded = load_advisor(path)
    assert isinstance(loaded, LogisticRegressionAdvisor)
    for ex in examples:
        assert trained.predict(ex.features) == loaded.predict(ex.features)


@pytest.mark.skipif(not HAS_CUDA_ADVISOR, reason="torch not installed")
def test_save_cuda_advisor_rejects_unknown_device(tmp_path: Path) -> None:
    """PR #996 review (P2): `device` must be `cuda` or `cpu`; passing
    anything else is rejected so the audit contract stays narrow."""
    examples = _separable_dataset()
    trained, _ = train_cuda_logistic(examples, epochs=10, seed=0)
    path = tmp_path / "bad.json"
    with pytest.raises(ValueError, match="unexpected training device"):
        save_cuda_advisor(trained, path, device="rocm")


@pytest.mark.skipif(not HAS_CUDA_ADVISOR, reason="torch not installed")
def test_save_cuda_advisor_records_training_device_not_save_host(tmp_path: Path) -> None:
    """PR #996 review (P2): the audit `device` field reflects the
    device passed in (i.e. where training ran), not whatever device
    happens to be available at save time. Pass an explicit ``device``
    that disagrees with the live host and confirm the file records
    the passed value."""
    examples = _separable_dataset()
    trained, _ = train_cuda_logistic(examples, epochs=10, seed=0)
    # Force the audit value to "cpu" regardless of host availability;
    # the saver must honor what the caller declared rather than
    # recomputing torch.cuda.is_available().
    path = tmp_path / "advisor.json"
    save_cuda_advisor(trained, path, device="cpu")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["device"] == "cpu"


@pytest.mark.skipif(not HAS_CUDA_ADVISOR, reason="torch not installed")
def test_train_cuda_logistic_raises_on_empty_dataset() -> None:
    with pytest.raises(ValueError, match="no labeled examples"):
        train_cuda_logistic([])


@pytest.mark.skipif(not HAS_CUDA_ADVISOR, reason="torch not installed")
def test_cli_train_advisor_cuda_writes_checkpoint(tmp_path: Path) -> None:
    """End-to-end CLI: ``--cuda --checkpoint`` writes a checkpoint
    that load_advisor accepts."""
    src = tmp_path / "data.jsonl"
    with src.open("w", encoding="utf-8") as fh:
        for i in range(20):
            fh.write(json.dumps(_row(f"hot{i}", "consolidated", use_count=10)) + "\n")
        for i in range(20):
            fh.write(json.dumps(_row(f"cold{i}", "pruned", use_count=0)) + "\n")
    checkpoint = tmp_path / "cuda-advisor.json"
    metrics = tmp_path / "metrics.json"
    result = CliRunner().invoke(
        app,
        [
            "hermes",
            "train-advisor",
            "--data",
            str(src),
            "--cuda",
            "--output",
            str(metrics),
            "--checkpoint",
            str(checkpoint),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["advisor_kind"] == "cuda_logistic_regression"
    assert payload["backend"] == "cuda"
    assert checkpoint.exists()
    loaded = load_advisor(checkpoint)
    assert isinstance(loaded, LogisticRegressionAdvisor)
