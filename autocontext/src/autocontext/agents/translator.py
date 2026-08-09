"""StrategyTranslator — extracts structured JSON strategy from free-form competitor output."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from autocontext.agents.subagent_runtime import SubagentRuntime, SubagentTask
from autocontext.agents.translator_simplification import extract_strategy_deterministic
from autocontext.agents.types import RoleExecution
from autocontext.harness.core.output_parser import extract_json
from autocontext.harness.core.types import RoleUsage
from autocontext.strategy_interface import is_action_plan_interface


class StrategyTranslator:
    """Single-purpose agent that converts raw competitor text into a validated JSON strategy dict."""

    def __init__(self, runtime: SubagentRuntime, model: str, max_tokens: int = 1024) -> None:
        self.runtime = runtime
        self.model = model
        # AC-905: the old 400/200 budgets were the tightest in the codebase
        # and routinely truncated strategy JSON; the floor is now 1024.
        self.max_tokens = max_tokens

    def translate(self, raw_output: str, strategy_interface: str) -> tuple[dict[str, Any], RoleExecution]:
        deterministic = extract_strategy_deterministic(raw_output)
        if deterministic is not None and self._matches_strategy_interface(deterministic, strategy_interface):
            execution = RoleExecution(
                role="translator",
                content=json.dumps(deterministic, sort_keys=True),
                usage=RoleUsage(input_tokens=0, output_tokens=0, latency_ms=0, model="deterministic"),
                subagent_id="deterministic-extract",
                status="completed",
            )
            return deterministic, execution

        action_plan_interface = is_action_plan_interface(strategy_interface)
        prompt = (
            "Extract the strategy from the following competitor analysis as a JSON object.\n\n"
            f"Strategy interface (expected format):\n{strategy_interface}\n\n"
            f"Competitor output:\n{raw_output}\n\n"
            "Return ONLY a valid JSON object with no markdown fences or explanation. "
            "Map any abbreviated or alternative field names to match the strategy interface. "
            + (
                "Preserve strings, arrays, and nested objects exactly as needed by the strategy interface."
                if action_plan_interface
                else "Include only numeric values."
            )
        )
        execution = self.runtime.run_task(
            SubagentTask(
                role="translator",
                model=self.model,
                prompt=prompt,
                max_tokens=self.max_tokens,
                temperature=0.0,
            )
        )
        # extract_json defaults to on_failure="raise": a strategy that fails
        # to parse must surface as an error, never as a silently-substituted
        # empty dict that then gets executed and scored as a legitimate
        # result. A JSONDecodeError (nothing parsed at all) is re-raised
        # as-is; a successful parse to a non-object (e.g. a bare array) is
        # reported with this method's own, more specific message instead of
        # the parser's generic one.
        try:
            decoded = extract_json(execution.content)
        except json.JSONDecodeError:
            raise
        except ValueError as exc:
            raise ValueError("translator did not return a JSON object") from exc
        # Type narrowing for mypy ONLY, not a runtime guard: extract_json is
        # declared `-> dict[str, Any] | None` because of the on_failure="none"
        # variant, but on the default "raise" path it either returns a dict or
        # raises, so the None arm is unreachable here. Asserts are stripped
        # under `python -O`; nothing below depends on this one executing.
        assert decoded is not None
        return decoded, execution

    @staticmethod
    def _matches_strategy_interface(strategy: Mapping[str, Any], strategy_interface: str) -> bool:
        """Return True when extracted keys already match the declared interface.

        This keeps deterministic extraction on the safe path only. If the
        competitor emits abbreviated or off-schema keys, we fall back to the
        translator model, which can canonicalize names.
        """
        if not strategy:
            return False
        keys = [str(key) for key in strategy]
        interface_keys = {
            *re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", strategy_interface),
            *re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:', strategy_interface),
        }
        if interface_keys:
            return all(key in interface_keys for key in keys)
        return all(re.search(rf"\b{re.escape(key)}\b", strategy_interface) is not None for key in keys)

    def translate_code(self, raw_output: str) -> tuple[dict[str, Any], RoleExecution]:
        """Extract executable Python code from competitor output.

        Returns {"__code__": "<source>"} as the strategy dict.
        No LLM call — code is extracted directly via regex.
        """
        code = self._extract_code_block(raw_output)
        if not code.strip():
            raise ValueError("no code block found in competitor output")
        execution = RoleExecution(
            role="translator",
            content=code,
            usage=RoleUsage(input_tokens=0, output_tokens=0, latency_ms=0, model="none"),
            subagent_id="code-extract",
            status="completed",
        )
        return {"__code__": code}, execution

    @staticmethod
    def _extract_code_block(text: str) -> str:
        """Extract code from markdown fences or return raw text."""
        match = re.search(r"```python\s*\n(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        match = re.search(r"```\s*\n(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text.strip()
