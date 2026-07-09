from __future__ import annotations

from autocontext.scenarios.agent_task import AgentTaskResult


def test_agent_task_result_has_optional_epoch_defaulting_none() -> None:
    r = AgentTaskResult(score=0.5, reasoning="x")
    assert r.evaluator_epoch is None
    r2 = AgentTaskResult(score=0.5, reasoning="x", evaluator_epoch="e1")
    assert r2.evaluator_epoch == "e1"
