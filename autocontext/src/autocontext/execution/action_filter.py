"""ActionFilterHarness — constrains LLM action selection to valid moves.

Wraps match execution to enumerate legal actions from the scenario or
loaded harness, format them as numbered prompts, and parse LLM responses.
Supports filter mode (LLM selects by index) and verify mode (LLM proposes,
harness validates).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from typing import Any

from autocontext.scenarios.base import ScenarioInterface

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


class ActionFilterHarness:
    """Constrains LLM action selection to legal moves via filter or verify mode."""

    def __init__(
        self,
        scenario: ScenarioInterface,
        harness_loader: Any | None = None,
    ) -> None:
        self._scenario = scenario
        self._harness_loader = harness_loader

    def get_legal_actions(self, state: Mapping[str, Any]) -> list[dict[str, Any]] | None:
        """Get legal actions, preferring the scenario method over harness loader.

        Returns None if enumeration is not supported by either source.
        """
        result = self._scenario.enumerate_legal_actions(state)
        if result is not None:
            return result
        if self._harness_loader is not None:
            return self._get_harness_actions(state)
        return None

    def format_action_prompt(self, actions: list[dict[str, Any]]) -> str:
        """Format actions as a numbered list for LLM selection.

        Returns a prompt string like:
            Available actions:
            1. aggression — Attack intensity (continuous [0.0, 1.0])
            2. defense — Defensive allocation (continuous [0.0, 1.0])
            Select an action by number:
        """
        if not actions:
            return "No actions available."
        if self._is_continuous_param_space(actions):
            lines = ["Provide a JSON object with all strategy parameters:"]
            example: dict[str, float] = {}
            for action in actions:
                name = str(action["action"])
                desc = action.get("description", "")
                low, high = action["range"]
                lines.append(f"- {name}: {desc} (range [{low}, {high}])")
                example[name] = round((float(low) + float(high)) / 2.0, 3)
            lines.append(f"Example: {json.dumps(example, sort_keys=True)}")
            lines.append("Respond with JSON only.")
            return "\n".join(lines)
        lines = ["Available actions:"]
        for i, action in enumerate(actions, 1):
            name = action.get("action", f"action_{i}")
            desc = action.get("description", "")
            extra = ""
            if "type" in action and action["type"] == "continuous" and "range" in action:
                r = action["range"]
                extra = f" (continuous [{r[0]}, {r[1]}])"
            elif "row" in action and "col" in action:
                extra = f" (row {action['row']}, col {action['col']})"
            line = f"{i}. {name}"
            if desc:
                line += f" — {desc}"
            line += extra
            lines.append(line)
        lines.append("Select an action by number:")
        return "\n".join(lines)

    def parse_action_selection(
        self,
        response: str,
        actions: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Parse LLM response to extract the selected action.

        Handles:
        - Numeric index (e.g., "1", "  2 ", "I choose 3")
        - Action name match (e.g., "aggression", "mobility_weight")
        - Returns None if no match found.
        """
        if not actions:
            return None

        if self._is_continuous_param_space(actions):
            return self._parse_continuous_selection(response, actions)

        # Try numeric index first
        match = re.search(r"\b(\d+)\b", response.strip())
        if match:
            idx = int(match.group(1))
            if 1 <= idx <= len(actions):
                return actions[idx - 1]

        # Try action name match
        response_lower = response.strip().lower()
        for action in actions:
            name = action.get("action", "")
            if name and name.lower() in response_lower:
                return action

        return None

    def verify_action(
        self,
        state: Mapping[str, Any],
        player_id: str,
        proposed: Mapping[str, Any],
    ) -> tuple[bool, str]:
        """Verify a proposed action using validate_actions.

        In verify mode, the LLM proposes freely and we check validity.
        """
        return self._scenario.validate_actions(state, player_id, proposed)

    def get_verify_feedback(
        self,
        reason: str,
        state: Mapping[str, Any],
    ) -> str:
        """Build feedback string for verify mode retries.

        Includes the rejection reason and available legal actions if enumerable.
        """
        parts = [f"Invalid action: {reason}"]
        legal = self.get_legal_actions(state)
        if legal:
            parts.append(self.format_action_prompt(legal))
        parts.append("Please try again.")
        return "\n".join(parts)

    def _get_harness_actions(self, state: Mapping[str, Any]) -> list[dict[str, Any]] | None:
        """Attempt to get legal actions from the harness loader."""
        if self._harness_loader is None:
            return None
        validators = getattr(self._harness_loader, "validators", [])
        for v in validators:
            fn = getattr(v, "enumerate_legal_actions", None)
            if fn is not None:
                try:
                    result = fn(dict(state))
                    if isinstance(result, list):
                        return result
                except Exception:
                    logger.warning("harness enumerate_legal_actions failed", exc_info=True)
        return None

    @staticmethod
    def _is_continuous_param_space(actions: list[dict[str, Any]]) -> bool:
        if not actions:
            return False
        for action in actions:
            if action.get("type") != "continuous":
                return False
            if not isinstance(action.get("action"), str):
                return False
            rng = action.get("range")
            if not isinstance(rng, (list, tuple)) or len(rng) != 2:
                return False
            if not all(isinstance(v, (int, float)) for v in rng):
                return False
        return True

    def _parse_continuous_selection(
        self,
        response: str,
        actions: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        payload = self._extract_json_object(response)
        if payload is None:
            return None

        strategy: dict[str, float] = {}
        for action in actions:
            key = str(action["action"])
            if key not in payload:
                return None
            raw = payload[key]
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                return None
            low, high = action["range"]
            value = float(raw)
            if value < float(low) or value > float(high):
                return None
            strategy[key] = value
        return strategy

    @staticmethod
    def _extract_json_object(response: str) -> dict[str, Any] | None:
        candidates: list[str] = []
        fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", response, flags=re.IGNORECASE)
        if fenced:
            candidates.append(fenced.group(1))
        start = response.find("{")
        end = response.rfind("}")
        if start != -1 and end > start:
            candidates.append(response[start : end + 1])
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None
