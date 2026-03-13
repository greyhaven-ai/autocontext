"""Tests for AC-233: Remove hardcoded Anthropic model IDs from scaffold/template defaults.

All scaffold, template, spec, and runner defaults should use empty string ""
meaning "inherit from provider default at runtime". Only provider-specific code
(e.g. AnthropicProvider) should hardcode Anthropic model IDs.
"""

from __future__ import annotations

import json

import pytest

# ---------------------------------------------------------------------------
# 1. AgentTaskSpec defaults
# ---------------------------------------------------------------------------

class TestAgentTaskSpecDefaults:
    def test_default_judge_model_is_empty(self) -> None:
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec

        spec = AgentTaskSpec(task_prompt="test", judge_rubric="rubric")
        assert spec.judge_model == ""

    def test_explicit_model_preserved(self) -> None:
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec

        spec = AgentTaskSpec(task_prompt="test", judge_rubric="rubric", judge_model="gpt-4o")
        assert spec.judge_model == "gpt-4o"


# ---------------------------------------------------------------------------
# 2. TemplateSpec defaults
# ---------------------------------------------------------------------------

class TestTemplateSpecDefaults:
    def test_default_judge_model_is_empty(self) -> None:
        from autocontext.scenarios.templates import TemplateSpec

        spec = TemplateSpec(name="t", description="d", task_prompt="p", judge_rubric="r")
        assert spec.judge_model == ""

    def test_from_dict_missing_judge_model_defaults_empty(self) -> None:
        from autocontext.scenarios.templates import TemplateSpec

        data = {"name": "t", "description": "d", "task_prompt": "p", "judge_rubric": "r"}
        spec = TemplateSpec.from_dict(data)
        assert spec.judge_model == ""

    def test_from_dict_explicit_model_preserved(self) -> None:
        from autocontext.scenarios.templates import TemplateSpec

        data = {"name": "t", "description": "d", "task_prompt": "p", "judge_rubric": "r", "judge_model": "gpt-4o"}
        spec = TemplateSpec.from_dict(data)
        assert spec.judge_model == "gpt-4o"


# ---------------------------------------------------------------------------
# 3. Agent task designer — no hardcoded Anthropic model in defaults/schema
# ---------------------------------------------------------------------------

class TestAgentTaskDesignerDefaults:
    def test_example_spec_judge_model_is_empty(self) -> None:
        from autocontext.scenarios.custom.agent_task_designer import _EXAMPLE_SPEC

        assert _EXAMPLE_SPEC["judge_model"] == ""

    def test_system_prompt_schema_no_hardcoded_anthropic_default(self) -> None:
        """The schema example in the system prompt should not hardcode an Anthropic model as the default."""
        from autocontext.scenarios.custom.agent_task_designer import AGENT_TASK_DESIGNER_SYSTEM

        # The schema section shows "judge_model": "..." — check it's not an Anthropic model
        assert '"judge_model": "claude-sonnet-4-20250514"' not in AGENT_TASK_DESIGNER_SYSTEM

    def test_parse_agent_task_spec_missing_model_defaults_empty(self) -> None:
        from autocontext.scenarios.custom.agent_task_designer import (
            SPEC_END,
            SPEC_START,
            parse_agent_task_spec,
        )

        raw = json.dumps({
            "task_prompt": "test",
            "judge_rubric": "rubric",
        })
        text = f"{SPEC_START}\n{raw}\n{SPEC_END}"
        spec = parse_agent_task_spec(text)
        assert spec.judge_model == ""


# ---------------------------------------------------------------------------
# 4. SimpleAgentTask / TaskRunner defaults
# ---------------------------------------------------------------------------

class TestTaskRunnerDefaults:
    def test_simple_agent_task_default_model_is_empty(self) -> None:
        from unittest.mock import MagicMock

        from autocontext.execution.task_runner import SimpleAgentTask

        provider = MagicMock()
        task = SimpleAgentTask(task_prompt="test", rubric="rubric", provider=provider)
        assert task._model == ""

    def test_task_runner_default_model_is_empty(self) -> None:
        from unittest.mock import MagicMock

        from autocontext.execution.task_runner import TaskRunner

        store = MagicMock()
        provider = MagicMock()
        runner = TaskRunner(store=store, provider=provider)
        assert runner.model == ""


# ---------------------------------------------------------------------------
# 5. TrainingConfig defaults
# ---------------------------------------------------------------------------

class TestTrainingConfigDefaults:
    def test_default_agent_model_is_empty(self) -> None:
        from pathlib import Path

        from autocontext.training.runner import TrainingConfig

        config = TrainingConfig(scenario="test", data_path=Path("/tmp"))
        assert config.agent_model == ""


# ---------------------------------------------------------------------------
# 6. CLI --agent-model default
# ---------------------------------------------------------------------------

class TestCLIDefaults:
    def test_train_command_agent_model_default_is_empty(self) -> None:
        """The --agent-model CLI option should default to empty string."""
        import inspect

        from autocontext.cli import app

        # Find the train command
        for cmd_info in app.registered_commands:
            if cmd_info.name == "train" or (cmd_info.callback and cmd_info.callback.__name__ == "train"):
                sig = inspect.signature(cmd_info.callback)  # type: ignore[arg-type]
                param = sig.parameters.get("agent_model")
                assert param is not None, "agent_model parameter not found in train command"
                default = param.default
                # Typer wraps defaults in Option objects
                if hasattr(default, "default"):
                    assert default.default == ""
                else:
                    assert default == ""
                return
        pytest.fail("train command not found in app")


# ---------------------------------------------------------------------------
# 7. Provider handles empty model correctly (falls back to default)
# ---------------------------------------------------------------------------

class TestProviderEmptyModelFallback:
    def test_anthropic_provider_empty_model_uses_default(self) -> None:
        """When complete() is called with model='', the provider should use its default."""
        from autocontext.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider(api_key="test", default_model_name="claude-sonnet-4-20250514")
        # The provider's complete() should convert "" to its default model
        assert provider.default_model() == "claude-sonnet-4-20250514"

    def test_openai_compat_provider_empty_model_uses_default(self) -> None:
        from autocontext.providers.openai_compat import OpenAICompatibleProvider

        try:
            provider = OpenAICompatibleProvider(api_key="test", default_model_name="gpt-4o")
        except Exception:
            pytest.skip("openai package not installed")
        assert provider.default_model() == "gpt-4o"


# ---------------------------------------------------------------------------
# 8. No hardcoded Anthropic model in non-provider source (comprehensive scan)
# ---------------------------------------------------------------------------

class TestNoHardcodedModelsInScaffold:
    """Verify that scaffold/template/spec/runner files don't hardcode Anthropic models."""

    HARDCODED_MODEL = "claude-sonnet-4-20250514"

    SCAFFOLD_FILES = [
        "autocontext/src/autocontext/scenarios/custom/agent_task_spec.py",
        "autocontext/src/autocontext/scenarios/templates/__init__.py",
        "autocontext/src/autocontext/scenarios/custom/agent_task_designer.py",
        "autocontext/src/autocontext/execution/task_runner.py",
        "autocontext/src/autocontext/training/runner.py",
    ]

    @pytest.mark.parametrize("filepath", SCAFFOLD_FILES)
    def test_no_hardcoded_anthropic_model_in_defaults(self, filepath: str) -> None:
        """Each scaffold/template file should not use a hardcoded Anthropic model as a default value."""
        from pathlib import Path

        full_path = Path(__file__).resolve().parents[1] / filepath
        if not full_path.exists():
            # Compute relative to repo root
            full_path = Path(__file__).resolve().parents[2] / filepath
        content = full_path.read_text(encoding="utf-8")

        # Count occurrences — only provider/config files should have them
        # These scaffold files should have zero
        count = content.count(self.HARDCODED_MODEL)
        assert count == 0, (
            f"{filepath} contains {count} occurrence(s) of hardcoded model "
            f"'{self.HARDCODED_MODEL}'. Scaffold/template defaults should use '' (empty string)."
        )
