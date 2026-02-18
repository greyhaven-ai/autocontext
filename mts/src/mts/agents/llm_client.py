from __future__ import annotations

import json
import time

from anthropic import Anthropic

from mts.harness.core.llm_client import LanguageModelClient
from mts.harness.core.types import ModelResponse, RoleUsage


class AnthropicClient(LanguageModelClient):
    def __init__(self, api_key: str):
        self._client = Anthropic(api_key=api_key)

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> ModelResponse:
        del role
        started = time.perf_counter()
        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        elapsed = int((time.perf_counter() - started) * 1000)
        text_segments: list[str] = []
        for block in response.content:
            maybe_text = getattr(block, "text", None)
            if isinstance(maybe_text, str):
                text_segments.append(maybe_text)
        text = "\n".join(text_segments).strip()
        usage = RoleUsage(
            input_tokens=getattr(response.usage, "input_tokens", 0),
            output_tokens=getattr(response.usage, "output_tokens", 0),
            latency_ms=elapsed,
            model=model,
        )
        return ModelResponse(text=text, usage=usage)

    def generate_multiturn(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> ModelResponse:
        del role
        started = time.perf_counter()
        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=messages,  # type: ignore[arg-type]
        )
        elapsed = int((time.perf_counter() - started) * 1000)
        text_segments: list[str] = []
        for block in response.content:
            maybe_text = getattr(block, "text", None)
            if isinstance(maybe_text, str):
                text_segments.append(maybe_text)
        text = "\n".join(text_segments).strip()
        usage = RoleUsage(
            input_tokens=getattr(response.usage, "input_tokens", 0),
            output_tokens=getattr(response.usage, "output_tokens", 0),
            latency_ms=elapsed,
            model=model,
        )
        return ModelResponse(text=text, usage=usage)


class DeterministicDevClient(LanguageModelClient):
    """Offline client for CI and local deterministic tests."""

    def __init__(self) -> None:
        self._rlm_turn_counter: int = 0

    def reset_rlm_turns(self) -> None:
        self._rlm_turn_counter = 0

    def generate_multiturn(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> ModelResponse:
        del max_tokens, temperature, role
        self._rlm_turn_counter += 1
        if self._rlm_turn_counter == 1:
            text = '<code>\nprint(type(answer))\nprint(answer)\n</code>'
        elif self._rlm_turn_counter == 2:
            text = (
                "<code>\n"
                "answer[\"content\"] = (\n"
                "    \"## Findings\\n\\n\"\n"
                "    \"- Strategy balances offense/defense.\\n\\n\"\n"
                "    \"## Root Causes\\n\\n\"\n"
                "    \"- Moderate aggressiveness.\\n\\n\"\n"
                "    \"## Actionable Recommendations\\n\\n\"\n"
                "    \"- Increase defensive weight.\"\n"
                ")\n"
                "answer[\"ready\"] = True\n"
                "</code>"
            )
        else:
            text = '<code>\nanswer["ready"] = True\n</code>'
        return ModelResponse(
            text=text,
            usage=RoleUsage(input_tokens=100, output_tokens=50, latency_ms=5, model=model),
        )

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> ModelResponse:
        del max_tokens, temperature, role
        prompt_lower = prompt.lower()
        # --- Scenario designer role ---
        if "scenario designer" in prompt_lower or "scenariospec" in prompt_lower:
            text = self._scenario_designer_response()
        # --- Translator role: extract JSON from competitor narrative ---
        elif "extract the strategy" in prompt_lower:
            text = self._translator_response(prompt_lower)
        # --- Competitor role: natural language strategy reasoning ---
        elif "describe your strategy" in prompt_lower:
            text = self._competitor_narrative(prompt_lower)
        elif "analyze strengths/failures" in prompt_lower:
            text = "## Findings\n\n- Strategy balances offense/defense.\n\n## Root Causes\n\n- Moderate aggressiveness."
        elif "you are the playbook coach" in prompt_lower or "update the playbook" in prompt_lower:
            text = (
                "<!-- PLAYBOOK_START -->\n"
                "## Strategy Updates\n\n- Keep defensive anchor.\n- Balance aggression with proportional defense.\n\n"
                "## Prompt Optimizations\n\n- Ask for concise JSON.\n\n"
                "## Next Generation Checklist\n\n- Stress test corner cases.\n"
                "<!-- PLAYBOOK_END -->\n\n"
                "<!-- LESSONS_START -->\n"
                "- When aggression exceeds 0.7 without proportional defense, win rate drops.\n"
                "- Defensive anchor above 0.5 stabilizes Elo across generations.\n"
                "<!-- LESSONS_END -->\n\n"
                "<!-- COMPETITOR_HINTS_START -->\n"
                "- Try aggression=0.60 with defense=0.55 for balanced scoring.\n"
                "- Keep path_bias between 0.50-0.60 for stability.\n"
                "<!-- COMPETITOR_HINTS_END -->"
            )
        elif "curator" in prompt_lower and "playbook quality" in prompt_lower:
            text = self._curator_playbook_response()
        elif "curator" in prompt_lower and "consolidat" in prompt_lower:
            text = self._curator_consolidate_response()
        else:
            tools_payload = {
                "tools": [
                    {
                        "name": "threat_assessor",
                        "description": "Estimate tactical risk from aggression, defense, and path bias.",
                        "code": (
                            "def run(inputs):\n"
                            "    aggression = float(inputs.get('aggression', 0.0))\n"
                            "    defense = float(inputs.get('defense', 0.0))\n"
                            "    path_bias = float(inputs.get('path_bias', 0.0))\n"
                            "    risk = max(0.0, min(1.0, aggression * 0.6 + (1.0 - defense) * 0.3 + (1.0 - path_bias) * 0.1))\n"
                            "    return {'risk': round(risk, 4)}"
                        ),
                    },
                    {
                        "name": "stability_analyzer",
                        "description": "Estimate opening stability from mobility, corner pressure, and stability weights.",
                        "code": (
                            "def run(inputs):\n"
                            "    mobility = float(inputs.get('mobility_weight', 0.0))\n"
                            "    corner = float(inputs.get('corner_weight', 0.0))\n"
                            "    stability = float(inputs.get('stability_weight', 0.0))\n"
                            "    score = max(0.0, min(1.0, mobility * 0.3 + corner * 0.4 + stability * 0.3))\n"
                            "    return {'stability_score': round(score, 4)}"
                        ),
                    },
                ]
            }
            text = (
                "## Observed Bottlenecks\n\n- Need richer replay telemetry.\n\n"
                "## Tool Proposals\n\n- Add analyzers for tactical confidence.\n\n"
                "## Impact Hypothesis\n\n- Better reliability over 3 generations.\n\n"
                f"```json\n{json.dumps(tools_payload, indent=2)}\n```"
            )
        return ModelResponse(
            text=text,
            usage=RoleUsage(
                input_tokens=max(1, len(prompt) // 6),
                output_tokens=max(1, len(text) // 6),
                latency_ms=5,
                model=model,
            ),
        )

    def _curator_playbook_response(self) -> str:
        return (
            "After comparing both playbooks, the proposed version maintains coverage "
            "while adding more specific actionable guidance.\n\n"
            "<!-- CURATOR_DECISION: accept -->\n"
            "<!-- CURATOR_SCORE: 7 -->\n"
        )

    def _curator_consolidate_response(self) -> str:
        return (
            "Consolidated lessons after removing duplicates and outdated entries:\n\n"
            "<!-- CONSOLIDATED_LESSONS_START -->\n"
            "- When aggression exceeds 0.7 without proportional defense, win rate drops.\n"
            "- Defensive anchor above 0.5 stabilizes Elo across generations.\n"
            "- Balance aggression with defense for consistent scoring.\n"
            "<!-- CONSOLIDATED_LESSONS_END -->\n"
            "<!-- LESSONS_REMOVED: 3 -->\n"
        )

    @staticmethod
    def _scenario_designer_response() -> str:
        spec = {
            "name": "resource_balance",
            "display_name": "Resource Balance",
            "description": (
                "A resource management scenario where agents balance mining, "
                "defense, and trade to maximize colony growth."
            ),
            "strategy_interface_description": (
                "Return JSON object with keys `mining`, `defense`, and `trade`, all floats in [0,1]. "
                "Constraint: mining + defense + trade <= 2.0."
            ),
            "evaluation_criteria": (
                "Optimize colony growth through efficient resource "
                "allocation across mining, defense, and trade."
            ),
            "strategy_params": [
                {
                    "name": "mining", "description": "Investment in resource extraction",
                    "min_value": 0.0, "max_value": 1.0, "default": 0.5,
                },
                {
                    "name": "defense", "description": "Investment in colony protection",
                    "min_value": 0.0, "max_value": 1.0, "default": 0.4,
                },
                {
                    "name": "trade", "description": "Investment in trade routes",
                    "min_value": 0.0, "max_value": 1.0, "default": 0.5,
                },
            ],
            "constraints": [
                {
                    "expression": "mining + defense + trade", "operator": "<=",
                    "threshold": 2.0, "description": "total allocation must be <= 2.0",
                },
            ],
            "environment_variables": [
                {"name": "resource_richness", "description": "Abundance of natural resources", "low": 0.2, "high": 0.8},
                {"name": "threat_level", "description": "External threat intensity", "low": 0.1, "high": 0.7},
            ],
            "scoring_components": [
                {
                    "name": "extraction_yield",
                    "description": "Mining output effectiveness",
                    "formula_terms": {"mining": 0.6, "trade": 0.4},
                    "noise_range": [-0.05, 0.05],
                },
                {
                    "name": "colony_safety",
                    "description": "Colony survival and protection",
                    "formula_terms": {"defense": 0.7, "mining": 0.3},
                    "noise_range": [-0.04, 0.04],
                },
                {
                    "name": "trade_profit",
                    "description": "Revenue from trade networks",
                    "formula_terms": {"trade": 0.55, "defense": 0.45},
                    "noise_range": [-0.03, 0.03],
                },
            ],
            "final_score_weights": {"extraction_yield": 0.4, "colony_safety": 0.35, "trade_profit": 0.25},
            "win_threshold": 0.55,
            "observation_constraints": [
                "Balance mining with defense to avoid vulnerability.",
                "Trade routes require baseline defense for security.",
            ],
        }
        return (
            "Here is the generated scenario spec:\n\n"
            "<!-- SCENARIO_SPEC_START -->\n"
            f"{json.dumps(spec, indent=2)}\n"
            "<!-- SCENARIO_SPEC_END -->"
        )

    @staticmethod
    def _is_othello(prompt_lower: str) -> bool:
        """Detect othello scenario via backtick-quoted interface fields."""
        return "`mobility_weight`" in prompt_lower

    def _competitor_narrative(self, prompt_lower: str) -> str:
        """Return narrative competitor response (no JSON)."""
        is_othello = self._is_othello(prompt_lower)
        if "retry attempt" in prompt_lower:
            if is_othello:
                return (
                    "After reviewing the previous attempt, I recommend adjusting weights: "
                    "mobility at 0.59 for better movement options, corner pressure at 0.64 "
                    "to dominate key positions, and stability at 0.56 for a solid foundation."
                )
            return (
                "Given the retry context, I recommend increasing aggression to 0.62 "
                "for more offensive pressure, lowering defense to 0.52 to free resources, "
                "and raising path_bias to 0.58 for better flanking angles."
            )
        if is_othello:
            if "stability_analyzer" in prompt_lower:
                return (
                    "Based on stability analysis, I recommend mobility_weight of 0.57 "
                    "for adequate movement, corner_weight of 0.66 for strong corner control, "
                    "and stability_weight of 0.62 for solid positional advantage."
                )
            return (
                "For the Othello opening, I recommend balanced weights: "
                "mobility at 0.55 for flexible play, corner pressure at 0.62 "
                "for key position control, and stability at 0.52 for moderate defense."
            )
        if "threat_assessor" in prompt_lower:
            return (
                "Using the threat assessment tool, I recommend aggression at 0.6 "
                "for calculated offense, defense at 0.56 for adequate protection, "
                "and path_bias at 0.62 for tactical flanking advantage."
            )
        return (
            "Based on the scenario state, I recommend aggression at 0.58 "
            "for offensive pressure, defense at 0.57 for base protection, "
            "and path_bias at 0.54 for slight flanking advantage."
        )

    def _translator_response(self, prompt_lower: str) -> str:
        """Return clean JSON for the translator role.

        Detect retry from competitor narrative phrases (not the competitor prompt).
        The translator prompt contains the competitor *output* text, so we look for
        phrases like "retry context" or "reviewing the previous attempt".
        """
        is_othello = self._is_othello(prompt_lower)
        is_retry = "retry context" in prompt_lower or "reviewing the previous attempt" in prompt_lower
        if is_retry:
            if is_othello:
                return json.dumps({"mobility_weight": 0.59, "corner_weight": 0.64, "stability_weight": 0.56})
            return json.dumps({"aggression": 0.62, "defense": 0.52, "path_bias": 0.58})
        if is_othello:
            if "stability analysis" in prompt_lower:
                return json.dumps({"mobility_weight": 0.57, "corner_weight": 0.66, "stability_weight": 0.62})
            return json.dumps({"mobility_weight": 0.55, "corner_weight": 0.62, "stability_weight": 0.52})
        if "threat assessment" in prompt_lower:
            return json.dumps({"aggression": 0.6, "defense": 0.56, "path_bias": 0.62})
        return json.dumps({"aggression": 0.58, "defense": 0.57, "path_bias": 0.54})


__all__ = ["LanguageModelClient", "ModelResponse", "AnthropicClient", "DeterministicDevClient"]
