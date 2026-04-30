"""Domain-agnostic REPL session for multi-turn LLM exploration."""

from __future__ import annotations

import ast
import hashlib
import json
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

_CODE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<code>(.*?)</code>", re.DOTALL | re.IGNORECASE),
    re.compile(r"```[ \t]*(?:python|py)[^\n`]*\r?\n(.*?)```", re.DOTALL | re.IGNORECASE),
    re.compile(r"```[ \t]*\r?\n(.*?)```", re.DOTALL),
)
_FINAL_ANSWER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<final_answer>(.*?)</final_answer>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<answer>(.*?)</answer>", re.DOTALL | re.IGNORECASE),
)
_NATURAL_CLOSURE_RE = re.compile(
    r"(?:^|\b)(final answer:|the answer is|in summary,|i['’]?m confident the answer is)\s*(?P<body>.*)",
    re.IGNORECASE | re.DOTALL,
)
_MUTATING_AST_NODES = (
    ast.Assign,
    ast.AnnAssign,
    ast.AugAssign,
    ast.Delete,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Import,
    ast.ImportFrom,
    ast.Global,
    ast.Nonlocal,
    ast.Return,
    ast.Raise,
    ast.Try,
)
_MUTATING_METHODS = {
    "add",
    "append",
    "clear",
    "discard",
    "extend",
    "insert",
    "pop",
    "popitem",
    "remove",
    "setdefault",
    "sort",
    "update",
}
_NO_PROGRESS_TURN_LIMIT = 3


def _extract_code_block(text: str) -> str | None:
    """Extract the first supported REPL code block, preserving legacy priority."""
    for pattern in _CODE_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            return match.group(1).strip()
    return None


def _extract_final_answer_marker(text: str) -> str | None:
    for pattern in _FINAL_ANSWER_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            return match.group(1).strip()
    return None


def _natural_closure_content(text: str) -> str | None:
    match = _NATURAL_CLOSURE_RE.search(text)
    if match is None:
        return None
    body = match.group("body").strip()
    return body or text.strip()


def _is_read_only_code(code: str) -> bool:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, _MUTATING_AST_NODES):
            return False
        if isinstance(node, ast.NamedExpr):
            return False
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in _MUTATING_METHODS:
                return False
    return True


def _set_answer_content(namespace: dict[str, Any], content: str) -> None:
    answer = namespace.get("answer")
    if not isinstance(answer, dict):
        answer = {"content": "", "ready": False}
        namespace["answer"] = answer
    answer["content"] = content


def _answer_content(namespace: dict[str, Any]) -> str:
    answer = namespace.get("answer")
    if not isinstance(answer, dict):
        return ""
    content = answer.get("content", "")
    return content if isinstance(content, str) else str(content)


def _snapshot_value(value: Any) -> Any:
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, int | float | bool) or value is None:
        return value
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda item: str(item[0]))[:25]
        return {
            "type": "dict",
            "length": len(value),
            "items": {str(k): _snapshot_value(v) for k, v in items},
        }
    if isinstance(value, list | tuple):
        return {
            "type": type(value).__name__,
            "length": len(value),
            "items": [_snapshot_value(item) for item in value[:10]],
        }
    if isinstance(value, set):
        sample = sorted(repr(item)[:200] for item in value)[:10]
        return {"type": "set", "length": len(value), "items": sample}
    return {"type": type(value).__name__, "repr": repr(value)[:200]}


def _progress_signature(namespace: dict[str, Any]) -> str:
    values = {
        str(key): _snapshot_value(value)
        for key, value in sorted(namespace.items(), key=lambda item: str(item[0]))
        if not str(key).startswith("__") and key not in {"get_history", "llm_batch"}
    }
    payload = {
        "values": values,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=repr).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _turn_progress_signature(
    *,
    namespace: dict[str, Any],
    code: str,
    stdout: str,
    error: str | None,
) -> str:
    payload = {
        "namespace": _progress_signature(namespace),
        "code": code,
        "stdout": stdout,
        "error": error or "",
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=repr).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
        finalize_reason = ""
        last_observed_content = ""
        no_progress_turns = 0
        previous_no_progress_signature = ""

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
            if assistant_text.strip():
                last_observed_content = assistant_text.strip()

            marked_answer = _extract_final_answer_marker(assistant_text)
            if marked_answer:
                _set_answer_content(self._worker.namespace, marked_answer)
                status = "soft_finalized"
                finalize_reason = "final_answer_marker"
                logger.info("RLM %s soft-finalized on turn %d via final-answer marker", self._role, turn)
                break

            code = _extract_code_block(assistant_text)
            natural_answer = _natural_closure_content(assistant_text)
            if natural_answer is not None and (code is None or _is_read_only_code(code)):
                _set_answer_content(self._worker.namespace, natural_answer)
                status = "soft_finalized"
                finalize_reason = "natural_language_closure"
                logger.info("RLM %s soft-finalized on turn %d via natural closure", self._role, turn)
                break

            if code is not None:
                result = self._worker.run_code(ReplCommand(code))

                self.execution_history.append(ExecutionRecord(
                    turn=turn,
                    code=code,
                    stdout=result.stdout,
                    error=result.error,
                    answer_ready=result.answer.get("ready", False),
                ))
                answer_content = _answer_content(self._worker.namespace)
                if answer_content:
                    last_observed_content = answer_content
                elif result.stdout.strip():
                    last_observed_content = result.stdout.strip()

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
                    finalize_reason = "answer_ready"
                    break

                no_progress_candidate = not result.stdout.strip() and result.error is None
                current_progress_signature = _turn_progress_signature(
                    namespace=self._worker.namespace,
                    code=code,
                    stdout=result.stdout,
                    error=result.error,
                )
                if no_progress_candidate and current_progress_signature == previous_no_progress_signature:
                    no_progress_turns += 1
                elif no_progress_candidate:
                    no_progress_turns = 1
                    previous_no_progress_signature = current_progress_signature
                else:
                    no_progress_turns = 0
                    previous_no_progress_signature = ""
                if no_progress_turns >= _NO_PROGRESS_TURN_LIMIT and self._max_turns > _NO_PROGRESS_TURN_LIMIT:
                    status = "soft_finalized"
                    finalize_reason = "no_progress"
                    if not _answer_content(self._worker.namespace) and last_observed_content:
                        _set_answer_content(self._worker.namespace, last_observed_content)
                    logger.info(
                        "RLM %s soft-finalized on turn %d after %d no-progress turns",
                        self._role,
                        turn,
                        no_progress_turns,
                    )
                    break
            else:
                # Model didn't emit code — nudge it
                messages.append({
                    "role": "user",
                    "content": "Please write code inside <code> tags or a ```python fenced block "
                    'to continue your analysis, or set answer["ready"] = True to finalize.',
                })
        else:
            status = "truncated"
            logger.warning("RLM %s hit max_turns=%d without finalizing", self._role, self._max_turns)

        answer = self._worker.namespace.get("answer", {"content": "", "ready": False})
        content = answer.get("content", "") if isinstance(answer, dict) else ""
        if not content and last_observed_content:
            content = last_observed_content
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
            metadata={"finalize_reason": finalize_reason} if finalize_reason else {},
        )
