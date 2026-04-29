from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from autocontext.execution.judge import LLMJudge
from autocontext.harness.core.llm_client import LanguageModelClient
from autocontext.harness.core.types import ModelResponse, RoleUsage
from autocontext.providers.base import CompletionResult, LLMProvider
from autocontext.scenarios.base import Observation
from autocontext.storage.artifacts import ArtifactStore


class _Provider(LLMProvider):
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> CompletionResult:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        return CompletionResult(text=self.response, model=model or "stub")

    def default_model(self) -> str:
        return "stub"


class _Client(LanguageModelClient):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> ModelResponse:
        self.calls.append(
            {
                "model": model,
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "role": role,
            }
        )
        return ModelResponse(
            text=f"response to {prompt}",
            usage=RoleUsage(input_tokens=1, output_tokens=2, latency_ms=3, model=model),
            metadata={"inner": True},
        )


def _judge_response(score: float = 0.7, reasoning: str = "ok") -> str:
    payload = {"score": score, "reasoning": reasoning, "dimensions": {"quality": score}}
    return f"<!-- JUDGE_RESULT_START -->\n{json.dumps(payload)}\n<!-- JUDGE_RESULT_END -->"


def _artifact_store(tmp_path: Path, hook_bus: Any | None = None) -> ArtifactStore:
    return ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        hook_bus=hook_bus,
    )


def test_hook_bus_dispatches_in_order_and_collects_handler_errors() -> None:
    from autocontext.extensions import HookBus, HookEvents, HookResult

    bus = HookBus()
    calls: list[str] = []

    def first(event: Any) -> HookResult:
        calls.append("first")
        return HookResult(payload={"value": event.payload["value"] + "a"})

    def broken(event: Any) -> None:
        calls.append("broken")
        raise RuntimeError("boom")

    def last(event: Any) -> HookResult:
        calls.append("last")
        return HookResult(payload={"value": event.payload["value"] + "b"})

    bus.on(HookEvents.CONTEXT, first)
    bus.on(HookEvents.CONTEXT, broken)
    bus.on(HookEvents.CONTEXT, last)

    event = bus.emit(HookEvents.CONTEXT, {"value": ""})

    assert calls == ["first", "broken", "last"]
    assert event.payload["value"] == "ab"
    assert len(event.errors) == 1
    assert "boom" in event.errors[0].message


def test_hook_bus_can_fail_fast() -> None:
    from autocontext.extensions import HookBus, HookEvents

    bus = HookBus(fail_fast=True)

    def broken(event: Any) -> None:
        raise RuntimeError("stop")

    bus.on(HookEvents.CONTEXT, broken)

    with pytest.raises(RuntimeError, match="stop"):
        bus.emit(HookEvents.CONTEXT, {})


def test_extension_loader_registers_module_factory(tmp_path: Path) -> None:
    from autocontext.extensions import HookBus, HookEvents, load_extensions

    module_path = tmp_path / "demo_extension.py"
    module_path.write_text(
        "def register(api):\n"
        "    def handler(event):\n"
        "        event.payload['loaded'] = True\n"
        "    api.on('context', handler)\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(tmp_path))
    try:
        bus = HookBus()
        loaded = load_extensions("demo_extension:register", bus)
        event = bus.emit(HookEvents.CONTEXT, {})
    finally:
        sys.path.remove(str(tmp_path))

    assert loaded == ["demo_extension:register"]
    assert event.payload["loaded"] is True


def test_extension_api_supports_decorator_registration(tmp_path: Path) -> None:
    from autocontext.extensions import HookBus, HookEvents, load_extensions

    module_path = tmp_path / "decorator_extension.py"
    module_path.write_text(
        "def register(api):\n"
        "    @api.on('context')\n"
        "    def handler(event):\n"
        "        event.payload['decorated'] = True\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(tmp_path))
    try:
        bus = HookBus()
        load_extensions("decorator_extension:register", bus)
        event = bus.emit(HookEvents.CONTEXT, {})
    finally:
        sys.path.remove(str(tmp_path))

    assert event.payload["decorated"] is True


def test_language_model_client_hooks_can_transform_request_and_response() -> None:
    from autocontext.extensions import HookBus, HookedLanguageModelClient, HookEvents, HookResult

    bus = HookBus()
    inner = _Client()

    def before(event: Any) -> HookResult:
        return HookResult(payload={"prompt": event.payload["prompt"] + " plus hook", "model": "hook-model"})

    def after(event: Any) -> HookResult:
        return HookResult(payload={"text": event.payload["text"].upper(), "metadata": {"hooked": True}})

    bus.on(HookEvents.BEFORE_PROVIDER_REQUEST, before)
    bus.on(HookEvents.AFTER_PROVIDER_RESPONSE, after)

    client = HookedLanguageModelClient(inner, bus)
    response = client.generate(model="m", prompt="hello", max_tokens=10, temperature=0.1, role="analyst")

    assert inner.calls[0]["prompt"] == "hello plus hook"
    assert inner.calls[0]["model"] == "hook-model"
    assert response.text == "RESPONSE TO HELLO PLUS HOOK"
    assert response.metadata["hooked"] is True
    assert response.metadata["inner"] is True


def test_prompt_hooks_transform_components_prompts_and_compaction() -> None:
    from autocontext.extensions import HookBus, HookEvents, HookResult
    from autocontext.prompts.templates import build_prompt_bundle

    bus = HookBus()

    def before_compaction(event: Any) -> HookResult:
        components = dict(event.payload["components"])
        components["playbook"] = "hook playbook"
        return HookResult(payload={"components": components})

    def after_context(event: Any) -> HookResult:
        roles = dict(event.payload["roles"])
        roles["competitor"] += "\nHOOKED CONTEXT"
        return HookResult(payload={"roles": roles})

    bus.on(HookEvents.BEFORE_COMPACTION, before_compaction)
    bus.on(HookEvents.CONTEXT, after_context)

    prompts = build_prompt_bundle(
        scenario_rules="rules",
        strategy_interface="interface",
        evaluation_criteria="criteria",
        previous_summary="summary",
        observation=Observation(narrative="narrative", state={}, constraints=[]),
        current_playbook="raw playbook",
        available_tools="tools",
        hook_bus=bus,
    )

    assert "hook playbook" in prompts.competitor
    assert "HOOKED CONTEXT" in prompts.competitor


def test_judge_hooks_transform_prompt_and_response() -> None:
    from autocontext.extensions import HookBus, HookEvents, HookResult

    bus = HookBus()
    provider = _Provider(_judge_response(score=0.2, reasoning="original"))

    def before(event: Any) -> HookResult:
        return HookResult(payload={"user_prompt": event.payload["user_prompt"] + "\nHOOKED JUDGE"})

    def after(event: Any) -> HookResult:
        return HookResult(payload={"response_text": _judge_response(score=0.95, reasoning="overridden")})

    bus.on(HookEvents.BEFORE_JUDGE, before)
    bus.on(HookEvents.AFTER_JUDGE, after)

    judge = LLMJudge(model="judge", rubric="rubric", provider=provider, hook_bus=bus)
    result = judge.evaluate("task", "output")

    assert "HOOKED JUDGE" in provider.calls[0]["user_prompt"]
    assert result.score == 0.95
    assert result.reasoning == "overridden"


def test_artifact_write_hooks_can_modify_and_block_writes(tmp_path: Path) -> None:
    from autocontext.extensions import HookBus, HookEvents, HookResult

    bus = HookBus()

    def mutate(event: Any) -> HookResult | None:
        if event.payload["format"] == "markdown":
            return HookResult(payload={"content": event.payload["content"] + "\nfrom hook"})
        if event.payload["path"].endswith("blocked.json"):
            return HookResult(block=True, reason="blocked by extension")
        return None

    bus.on(HookEvents.ARTIFACT_WRITE, mutate)
    store = _artifact_store(tmp_path, hook_bus=bus)

    markdown_path = tmp_path / "knowledge" / "grid_ctf" / "notes.md"
    store.write_markdown(markdown_path, "hello")

    assert markdown_path.read_text(encoding="utf-8") == "hello\nfrom hook\n"

    with pytest.raises(RuntimeError, match="blocked by extension"):
        store.write_json(tmp_path / "knowledge" / "grid_ctf" / "blocked.json", {"ok": True})
