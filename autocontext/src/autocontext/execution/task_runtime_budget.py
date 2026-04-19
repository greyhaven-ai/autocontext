from __future__ import annotations

TASK_LIKE_EXECUTION_SYSTEM_PROMPT = (
    "Complete the task precisely. Return only the required answer. Be concise and stop once the task requirements are satisfied."
)

_TASK_LIKE_COMPLETION_MAX_TOKENS: dict[str, int] = {
    "free_text": 1600,
    "json_schema": 2200,
    "code": 2600,
}
_DEFAULT_TASK_LIKE_COMPLETION_MAX_TOKENS = 1800


def resolve_task_like_completion_max_tokens(state: dict, prompt: str) -> int:
    """Return a bounded response budget for task-like initial completions.

    Solve-created agent-task scenarios can ask for large structured outputs. Letting
    the runtime default to a very large completion budget increases latency and can
    trigger live Pi timeouts for roadmap-style prompts. This helper centralizes a
    tighter, output-format-aware cap for the first completion.
    """
    del prompt
    output_format = state.get("output_format")
    if isinstance(output_format, str):
        normalized = output_format.strip().lower()
        if normalized in _TASK_LIKE_COMPLETION_MAX_TOKENS:
            return _TASK_LIKE_COMPLETION_MAX_TOKENS[normalized]
    return _DEFAULT_TASK_LIKE_COMPLETION_MAX_TOKENS
