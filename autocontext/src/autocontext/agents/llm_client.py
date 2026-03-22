from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import anthropic
from anthropic import Anthropic

from autocontext.config.settings import AppSettings
from autocontext.harness.core.llm_client import LanguageModelClient
from autocontext.harness.core.types import ModelResponse, RoleUsage
from autocontext.providers.base import ProviderError
from autocontext.providers.mlx_provider import MLXProvider  # type: ignore[import-untyped]
from autocontext.providers.retry import _is_transient

LOGGER = logging.getLogger(__name__)


class AnthropicClient(LanguageModelClient):
    def __init__(
        self,
        api_key: str,
        *,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        backoff_factor: float = 2.0,
    ) -> None:
        self._client = Anthropic(api_key=api_key)
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor

    def _messages_create_with_retry(self, **kwargs: Any) -> Any:
        delay = self.base_delay
        last_error: anthropic.APIError | None = None

        for attempt in range(1 + self.max_retries):
            try:
                return self._client.messages.create(**kwargs)
            except anthropic.APIError as exc:
                last_error = exc
                is_transient = _is_transient(exc)
                if attempt == self.max_retries or not is_transient:
                    if not is_transient:
                        LOGGER.warning(
                            "non-transient Anthropic error (attempt %d), not retrying: %s",
                            attempt + 1,
                            exc,
                        )
                    break

                LOGGER.warning(
                    "transient Anthropic error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1,
                    1 + self.max_retries,
                    delay,
                    exc,
                )
                time.sleep(delay)
                delay = min(delay * self.backoff_factor, self.max_delay)

        raise last_error  # type: ignore[misc]

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
        response = self._messages_create_with_retry(
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
        response = self._messages_create_with_retry(
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


class MLXClient(LanguageModelClient):
    """LanguageModelClient adapter over the local MLX provider."""

    def __init__(self, model_path: str, *, temperature: float = 0.8, max_tokens: int = 512) -> None:
        self._provider = MLXProvider(model_path=model_path, temperature=temperature, max_tokens=max_tokens)

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> ModelResponse:
        del model, role
        started = time.perf_counter()
        try:
            result = self._provider.complete("", prompt, temperature=temperature, max_tokens=max_tokens)
        except ProviderError as exc:
            raise RuntimeError(str(exc)) from exc
        elapsed = int((time.perf_counter() - started) * 1000)
        usage = RoleUsage(
            input_tokens=result.usage.get("input_tokens", 0),
            output_tokens=result.usage.get("output_tokens", 0),
            latency_ms=elapsed,
            model=result.model or self._provider.default_model(),
        )
        return ModelResponse(text=result.text, usage=usage)

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
        user_parts = [m["content"] for m in messages if m["role"] == "user"]
        combined = "\n\n".join(user_parts)
        prompt = f"{system}\n\n{combined}" if system else combined
        return self.generate(
            model=model,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )


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
        # --- Code strategy competitor role ---
        elif "code strategy mode" in prompt_lower:
            text = self._code_strategy_response(prompt_lower)
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
        elif "skeptic" in prompt_lower and "red-team" in prompt_lower:
            text = self._skeptic_review_response()
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

    def _skeptic_review_response(self) -> str:
        return (
            "The proposed candidate shows moderate risk. Some patterns may be overfitting "
            "to specific opponent types observed in recent tournaments.\n\n"
            "<!-- SKEPTIC_RISK: medium -->\n"
            "<!-- SKEPTIC_CONCERNS_START -->\n"
            "- Score improvement may be fragile against diverse opponents\n"
            "- Pattern similarity to generation N-2 suggests recycled approach\n"
            "<!-- SKEPTIC_CONCERNS_END -->\n"
            "<!-- SKEPTIC_RECOMMENDATION: caution -->\n"
            "<!-- SKEPTIC_CONFIDENCE: 6 -->\n"
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

    def _code_strategy_response(self, prompt_lower: str) -> str:
        """Return a code strategy wrapped in python fences."""
        if self._is_othello(prompt_lower):
            return (
                "Based on the observation, I'll dynamically weight the parameters:\n\n"
                "```python\n"
                "obs = observation\n"
                "density = obs['state'].get('resource_density', 0.5)\n"
                "result = {\n"
                "    'mobility_weight': 0.55 + density * 0.1,\n"
                "    'corner_weight': 0.62,\n"
                "    'stability_weight': 0.52 + (1.0 - density) * 0.1,\n"
                "}\n"
                "```"
            )
        return (
            "I'll adapt my strategy based on the game state:\n\n"
            "```python\n"
            "obs = observation\n"
            "density = obs['state'].get('resource_density', 0.5)\n"
            "result = {\n"
            "    'aggression': 0.58 + density * 0.1,\n"
            "    'defense': 0.57 - density * 0.05,\n"
            "    'path_bias': 0.54,\n"
            "}\n"
            "```"
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


def build_client_from_settings(
    settings: AppSettings,
    *,
    scenario_name: str = "",
) -> LanguageModelClient:
    """Construct a LanguageModelClient from AppSettings."""
    if settings.agent_provider == "anthropic":
        api_key = settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError(
                "AUTOCONTEXT_ANTHROPIC_API_KEY or ANTHROPIC_API_KEY is required "
                "when AUTOCONTEXT_AGENT_PROVIDER=anthropic"
            )
        return AnthropicClient(api_key=api_key)
    if settings.agent_provider == "deterministic":
        return DeterministicDevClient()
    if settings.agent_provider == "agent_sdk":
        from autocontext.agents.agent_sdk_client import AgentSdkClient, AgentSdkConfig

        sdk_config = AgentSdkConfig(connect_mcp_server=settings.agent_sdk_connect_mcp)
        return AgentSdkClient(config=sdk_config)
    if settings.agent_provider == "mlx":
        if not settings.mlx_model_path:
            raise ValueError("AUTOCONTEXT_MLX_MODEL_PATH is required when AUTOCONTEXT_AGENT_PROVIDER=mlx")
        return MLXClient(
            model_path=settings.mlx_model_path,
            temperature=settings.mlx_temperature,
            max_tokens=settings.mlx_max_tokens,
        )
    if settings.agent_provider in ("openai", "openai-compatible", "ollama", "vllm"):
        from autocontext.agents.provider_bridge import ProviderBridgeClient
        from autocontext.providers.registry import create_provider

        api_key = settings.agent_api_key or settings.judge_api_key
        base_url = settings.agent_base_url or settings.judge_base_url
        provider = create_provider(
            provider_type=settings.agent_provider,
            api_key=api_key,
            base_url=base_url or None,
            model=settings.agent_default_model,
        )
        return ProviderBridgeClient(provider, use_provider_default_model=True)
    if settings.agent_provider == "pi":
        from autocontext.agents.provider_bridge import RuntimeBridgeClient
        from autocontext.providers.scenario_routing import resolve_pi_model
        from autocontext.runtimes.pi_cli import PiCLIConfig, PiCLIRuntime
        from autocontext.training.model_registry import ModelRegistry

        resolved_model = settings.pi_model
        if scenario_name or settings.pi_model:
            try:
                handoff = resolve_pi_model(
                    ModelRegistry(settings.knowledge_root),
                    scenario=scenario_name,
                    backend="mlx",
                    manual_override=settings.pi_model or None,
                )
            except Exception:
                handoff = None
            if handoff is not None:
                resolved_model = handoff.checkpoint_path

        config = PiCLIConfig(
            pi_command=settings.pi_command,
            timeout=settings.pi_timeout,
            workspace=settings.pi_workspace,
            model=resolved_model,
        )
        return RuntimeBridgeClient(PiCLIRuntime(config))
    if settings.agent_provider == "pi-rpc":
        from autocontext.agents.provider_bridge import RuntimeBridgeClient
        from autocontext.runtimes.pi_rpc import PiRPCConfig, PiRPCRuntime

        rpc_config = PiRPCConfig(
            endpoint=settings.pi_rpc_endpoint or "http://localhost:3284",
            api_key=settings.pi_rpc_api_key,
            session_persistence=settings.pi_rpc_session_persistence,
        )
        return RuntimeBridgeClient(PiRPCRuntime(rpc_config))
    if settings.agent_provider == "hermes":
        from autocontext.agents.provider_bridge import RuntimeBridgeClient
        from autocontext.runtimes.hermes_cli import HermesCLIConfig, HermesCLIRuntime

        hermes_config = HermesCLIConfig(
            hermes_command=settings.hermes_command,
            model=settings.hermes_model,
            timeout=settings.hermes_timeout,
            workspace=settings.hermes_workspace,
            base_url=settings.hermes_base_url,
            api_key=settings.hermes_api_key,
        )
        return RuntimeBridgeClient(HermesCLIRuntime(hermes_config))
    raise ValueError(f"unsupported agent provider: {settings.agent_provider}")


__all__ = [
    "LanguageModelClient",
    "ModelResponse",
    "AnthropicClient",
    "DeterministicDevClient",
    "MLXClient",
    "build_client_from_settings",
]
