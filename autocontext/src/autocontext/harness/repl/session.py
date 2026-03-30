"""Domain-agnostic REPL session for multi-turn LLM exploration."""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from autocontext.harness.core.llm_client import LanguageModelClient
from autocontext.harness.core.types import RoleExecution, RoleUsage
from autocontext.harness.repl.types import ExecutionRecord, ReplCommand, ReplWorkerProtocol

logger = logging.getLogger(__name__)

_CODE_PATTERN = re.compile(r"<code>(.*?)</code>", re.DOTALL)


def make_llm_batch(
    client: LanguageModelClient,
    model: str,
    max_tokens: int = 1024,
    temperature: float = 0.1,
    max_workers: int = 4,
) -> Callable[[list[str]], list[str]]:
    """Create an ``llm_batch()`` callable for injection into the REPL namespace."""

    def llm_batch(prompts: list[str]) -> list[str]:
        if not prompts:
            return []
        workers = min(len(prompts), max_workers)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(
                    client.generate,
                    model=model,
                    prompt=p,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                for p in prompts
            ]
            results: list[str] = []
            for f in futures:
                try:
                    results.append(f.result().text)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("harness.repl.session: caught Exception", exc_info=True)
                    results.append(f"[llm_batch error: {exc}]")
            return results

    return llm_batch


class RlmSession:
    """Drives the multi-turn REPL conversation loop for one agent role."""

    def __init__(
        self,
        client: LanguageModelClient,
        worker: ReplWorkerProtocol,
        role: str,
        model: str,
        system_prompt: str,
        initial_user_message: str = "Begin exploring the data.",
        max_turns: int = 15,
        max_tokens_per_turn: int = 2048,
        temperature: float = 0.2,
        on_turn: Callable[[int, int, bool], None] | None = None,
    ) -> None:
        self._client = client
        self._worker = worker
        self._role = role
        self._model = model
        self._system = system_prompt
        self._initial_msg = initial_user_message
        self._max_turns = max_turns
        self._max_tokens = max_tokens_per_turn
        self._temperature = temperature
        self._on_turn = on_turn
        self.execution_history: list[ExecutionRecord] = []

    def run(self) -> RoleExecution:
        """Execute the full REPL loop and return a RoleExecution."""
        started = time.perf_counter()
        messages: list[dict[str, str]] = [{"role": "user", "content": self._initial_msg}]
        total_input = 0
        total_output = 0
        status = "completed"

        def _get_history() -> list[dict[str, Any]]:
            return [
                {
                    "turn": r.turn,
                    "code_preview": r.code[:200],
                    "stdout_preview": r.stdout[:200],
                    "error": r.error,
                }
                for r in self.execution_history
            ]

        self._worker.namespace["get_history"] = _get_history

        for turn in range(1, self._max_turns + 1):
            response = self._client.generate_multiturn(
                model=self._model,
                system=self._system,
                messages=messages,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
            )
            total_input += response.usage.input_tokens
            total_output += response.usage.output_tokens

            assistant_text = response.text
            messages.append({"role": "assistant", "content": assistant_text})

            code_match = _CODE_PATTERN.search(assistant_text)
            if code_match:
                code = code_match.group(1).strip()
                result = self._worker.run_code(ReplCommand(code))

                self.execution_history.append(ExecutionRecord(
                    turn=turn,
                    code=code,
                    stdout=result.stdout,
                    error=result.error,
                    answer_ready=result.answer.get("ready", False),
                ))

                # Build user feedback message
                parts: list[str] = []
                if result.stdout:
                    parts.append(f"[stdout]\n{result.stdout}")
                if result.error:
                    parts.append(f"[error]\n{result.error}")
                if not parts:
                    parts.append("[no output]")

                feedback = "\n\n".join(parts)
                messages.append({"role": "user", "content": feedback})

                if self._on_turn:
                    self._on_turn(turn, self._max_turns, result.answer.get("ready", False))

                if result.answer.get("ready"):
                    logger.debug("RLM %s finished on turn %d", self._role, turn)
                    break
            else:
                # Model didn't emit code — nudge it
                messages.append({
                    "role": "user",
                    "content": "Please write code inside <code> tags to continue your analysis, "
                    'or set answer["ready"] = True to finalize.',
                })
        else:
            status = "truncated"
            logger.warning("RLM %s hit max_turns=%d without finalizing", self._role, self._max_turns)

        answer = self._worker.namespace.get("answer", {"content": "", "ready": False})
        content = answer.get("content", "")
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        return RoleExecution(
            role=self._role,
            content=content,
            usage=RoleUsage(
                input_tokens=total_input,
                output_tokens=total_output,
                latency_ms=elapsed_ms,
                model=self._model,
            ),
            subagent_id=uuid.uuid4().hex[:10],
            status=status,
        )
