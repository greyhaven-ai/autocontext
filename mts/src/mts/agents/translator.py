"""StrategyTranslator — extracts structured JSON strategy from free-form competitor output."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from mts.agents.subagent_runtime import SubagentRuntime, SubagentTask
from mts.agents.types import RoleExecution
from mts.harness.core.output_parser import strip_json_fences as _harness_strip_fences


class StrategyTranslator:
    """Single-purpose agent that converts raw competitor text into a validated JSON strategy dict."""

    def __init__(self, runtime: SubagentRuntime, model: str) -> None:
        self.runtime = runtime
        self.model = model

    @staticmethod
    def _strip_fences(text: str) -> str:
        """Strip markdown code fences if present, returning the inner content."""
        return _harness_strip_fences(text)

    def translate(self, raw_output: str, strategy_interface: str) -> tuple[dict[str, Any], RoleExecution]:
        prompt = (
            "Extract the strategy from the following competitor analysis as a JSON object.\n\n"
            f"Strategy interface (expected format):\n{strategy_interface}\n\n"
            f"Competitor output:\n{raw_output}\n\n"
            "Return ONLY a valid JSON object with no markdown fences or explanation. "
            "Map any abbreviated or alternative field names "
            "to match the strategy interface. Include only numeric values."
        )
        execution = self.runtime.run_task(
            SubagentTask(
                role="translator",
                model=self.model,
                prompt=prompt,
                max_tokens=200,
                temperature=0.0,
            )
        )
        cleaned = self._strip_fences(execution.content)
        decoded = json.loads(cleaned)
        if not isinstance(decoded, Mapping):
            raise ValueError("translator did not return a JSON object")
        return dict(decoded), execution
