from __future__ import annotations

from autocontext.execution.evaluator_epoch import compute_evaluator_epoch
from autocontext.execution.rubric_calibration import run_judge_calibration
from autocontext.providers.callable_wrapper import CallableProvider


def test_calibration_report_carries_evaluator_epoch() -> None:
    provider = CallableProvider(lambda system, user: '{"score": 0.7, "reasoning": "ok"}', model_name="m")
    report = run_judge_calibration(
        domain="d",
        task_prompt="t",
        rubric="score correctness 0-1",
        provider=provider,
        model="m",
        calibration_examples=[
            {"id": "a", "agent_output": "a", "human_score": 0.7, "human_notes": "n"},
            {"id": "b", "agent_output": "b", "human_score": 0.3, "human_notes": "n"},
        ],
        repeat_judgments=1,
    )
    assert report is not None
    expected = compute_evaluator_epoch("score correctness 0-1", provider.name, "m").epoch_id
    assert report.evaluator_epoch == expected
