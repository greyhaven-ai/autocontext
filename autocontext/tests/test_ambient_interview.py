from __future__ import annotations

from autocontext.ambient.interview import InterviewAnswers, build_charter, run_interview


def _answers(**overrides: object) -> InterviewAnswers:
    base: dict[str, object] = dict(
        tier="oss",
        autonomy="train",
        enable_otel=True,
        enable_proxy=False,
        first_target_role="competitor@grid_ctf",
        base_model="Qwen/Qwen2.5-3B-Instruct",
        gpu_hours_per_window=8.0,
        window_hours=24,
        disk_quota_gb=200.0,
    )
    base.update(overrides)
    return InterviewAnswers(**base)  # type: ignore[arg-type]


def test_build_charter_includes_native_source_always() -> None:
    charter = build_charter(_answers(enable_otel=False, enable_proxy=False))
    assert [s.kind for s in charter.sources] == ["autocontext"]


def test_build_charter_adds_optional_sources() -> None:
    charter = build_charter(_answers(enable_otel=True, enable_proxy=True))
    assert [s.kind for s in charter.sources] == ["autocontext", "otel", "proxy"]


def test_build_charter_first_target() -> None:
    charter = build_charter(_answers())
    target = charter.targets[0]
    assert target.selector == "competitor@grid_ctf"
    assert target.eval_suite == "competitor_holdout"
    assert target.min_dataset_records == 500
    assert charter.autonomy == "train"


def test_run_interview_maps_prompts_to_answers() -> None:
    scripted = {
        "deployment tier": "oss",
        "autonomy": "propose",
        "enable otel source": "y",
        "enable llm proxy source": "n",
        "first target role": "competitor@grid_ctf",
        "base model": "Qwen/Qwen2.5-3B-Instruct",
        "gpu hours per window": "4",
        "window hours": "24",
        "disk quota gb": "100",
    }

    def prompt(question: str, default: str) -> str:
        for key, value in scripted.items():
            if key in question.lower():
                return value
        raise AssertionError(f"unexpected question: {question}")

    answers = run_interview(prompt)
    assert answers.autonomy == "propose"
    assert answers.enable_otel is True
    assert answers.enable_proxy is False
    assert answers.gpu_hours_per_window == 4.0
