"""R1 recipe pipeline: distillation cold-start -> RLVR resuming that adapter.

These pin the orchestration (path wiring + stage chaining) without MLX: the two stage
functions are monkeypatched, and we assert RLVR is invoked resuming from the distilled
adapter the distill stage produced.
"""

from __future__ import annotations

from pathlib import Path


def test_distilled_adapter_path_is_under_distill_stage() -> None:
    from autocontext.training.autoresearch.r1_pipeline import distilled_adapter_path

    p = distilled_adapter_path("/tmp/run")
    assert p == Path("/tmp/run") / "distill" / "adapters" / "adapters.safetensors"


def test_run_r1_pipeline_chains_distill_into_rlvr(monkeypatch, tmp_path) -> None:
    import autocontext.training.autoresearch.r1_pipeline as r1

    captured: dict = {}

    def fake_distill(*, scenario_name, output_dir, **kw):
        adapters = Path(output_dir) / "adapters"
        adapters.mkdir(parents=True, exist_ok=True)
        (adapters / "adapters.safetensors").write_text("fake-lora", encoding="utf-8")
        captured["distill_out"] = str(output_dir)
        return {"avg_score": 0.30, "valid_rate": 1.0}

    def fake_rlvr(*, scenario_name, output_dir, resume_adapter_file=None, **kw):
        captured["resume"] = resume_adapter_file
        captured["rlvr_out"] = str(output_dir)
        return {"avg_score": 0.50, "valid_rate": 1.0}

    monkeypatch.setattr(r1, "run_mlxlm_training", fake_distill)
    monkeypatch.setattr(r1, "run_grpo_training", fake_rlvr)

    out = r1.run_r1_pipeline(
        scenario_name="s",
        data_path=tmp_path / "data.jsonl",
        output_dir=tmp_path / "r1",
        base_model="m",
    )

    # RLVR resumes from the distillation cold-start adapter (the whole point)
    assert captured["resume"] is not None
    assert captured["resume"].endswith("distill/adapters/adapters.safetensors")
    # distinct stage dirs
    assert captured["distill_out"].endswith("distill")
    assert captured["rlvr_out"].endswith("rlvr")
    # pipeline reports both stages; headline score is the final (RLVR) score
    assert out["distill"]["avg_score"] == 0.30
    assert out["rlvr"]["avg_score"] == 0.50
    assert out["avg_score"] == 0.50


def test_run_r1_pipeline_skips_resume_if_distill_produced_no_adapter(monkeypatch, tmp_path) -> None:
    """If the distill stage produced no adapter, RLVR trains from base (resume=None) rather than crashing."""
    import autocontext.training.autoresearch.r1_pipeline as r1

    captured: dict = {}
    monkeypatch.setattr(r1, "run_mlxlm_training", lambda **kw: {"avg_score": 0.0, "valid_rate": 0.0})
    monkeypatch.setattr(
        r1, "run_grpo_training", lambda **kw: captured.update(resume=kw.get("resume_adapter_file")) or {"avg_score": 0.1}
    )

    r1.run_r1_pipeline(scenario_name="s", data_path=tmp_path / "d.jsonl", output_dir=tmp_path / "r1", base_model="m")
    assert captured["resume"] is None
