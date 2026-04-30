from __future__ import annotations

from typing import Any

from autocontext.agents.types import RoleExecution


def build_trial_summary(
    generation: int,
    history: list[Any],
    role_exec: RoleExecution,
) -> str:
    """Build a concise markdown summary of an RLM competitor session."""
    total_turns = len(history)
    code_runs = sum(1 for r in history if r.code)
    errors = sum(1 for r in history if r.error)
    lines = [
        f"### Generation {generation} — RLM competitor trial",
        f"- Turns: {total_turns}, code executions: {code_runs}, errors: {errors}",
        f"- Status: {role_exec.status}",
        f"- Latency: {role_exec.usage.latency_ms}ms",
    ]
    for rec in history:
        err_flag = " [ERROR]" if rec.error else ""
        ready_flag = " [READY]" if rec.answer_ready else ""
        code_preview = rec.code[:80].replace("\n", " ")
        lines.append(f"  - Turn {rec.turn}: `{code_preview}`{err_flag}{ready_flag}")
    return "\n".join(lines)
