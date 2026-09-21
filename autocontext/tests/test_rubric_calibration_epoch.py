from __future__ import annotations

from autocontext.execution.rubric_calibration import run_judge_calibration
from autocontext.providers.callable_wrapper import CallableProvider


def test_leave_one_out_report_cannot_claim_one_serving_epoch() -> None:
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
    assert report.evaluator_epoch is None
    assert report.metadata["identity_status"] == "mixed_or_unknown"
    assert len({e for epochs in report.metadata["per_anchor_epochs"].values() for e in epochs}) == 2
