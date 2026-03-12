"""Tests for Phase 2 — Code Strategies (translator, prompts, executor, routing)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from autocontext.agents.translator import StrategyTranslator
from autocontext.config.settings import AppSettings


class TestTranslateCode:
    """StrategyTranslator.translate_code() extracts code from competitor output."""

    def _make_translator(self) -> StrategyTranslator:
        """Create a translator with a dummy runtime (no LLM calls needed)."""
        runtime = MagicMock()
        return StrategyTranslator(runtime, model="test")

    def test_extracts_python_from_fenced_block(self) -> None:
        t = self._make_translator()
        raw = "Here is my strategy:\n\n```python\nresult = {'x': 1}\n```\n\nDone."
        strategy, exec_ = t.translate_code(raw)
        assert "__code__" in strategy
        assert "result = {'x': 1}" in strategy["__code__"]

    def test_extracts_from_plain_fenced_block(self) -> None:
        t = self._make_translator()
        raw = "Strategy:\n\n```\nresult = {'y': 2}\n```"
        strategy, exec_ = t.translate_code(raw)
        assert strategy["__code__"] == "result = {'y': 2}"

    def test_extracts_from_plain_text_without_fences(self) -> None:
        t = self._make_translator()
        raw = "result = {'z': 3}"
        strategy, exec_ = t.translate_code(raw)
        assert strategy["__code__"] == "result = {'z': 3}"

    def test_returns_code_key_dict(self) -> None:
        t = self._make_translator()
        raw = "```python\nresult = {}\n```"
        strategy, _ = t.translate_code(raw)
        assert list(strategy.keys()) == ["__code__"]

    def test_strips_leading_trailing_whitespace(self) -> None:
        t = self._make_translator()
        raw = "```python\n\n  result = {'a': 1}  \n\n```"
        strategy, _ = t.translate_code(raw)
        assert strategy["__code__"] == "result = {'a': 1}"

    def test_raises_on_empty_code(self) -> None:
        t = self._make_translator()
        raw = "```python\n\n\n```"
        with pytest.raises(ValueError, match="no code block found"):
            t.translate_code(raw)

    def test_raises_on_blank_input(self) -> None:
        t = self._make_translator()
        with pytest.raises(ValueError, match="no code block found"):
            t.translate_code("   ")

    def test_execution_has_translator_role(self) -> None:
        t = self._make_translator()
        raw = "```python\nresult = {}\n```"
        _, exec_ = t.translate_code(raw)
        assert exec_.role == "translator"
        assert exec_.status == "completed"


class TestCodeStrategySetting:
    def test_default_is_false(self) -> None:
        s = AppSettings()
        assert s.code_strategies_enabled is False

    def test_can_enable(self) -> None:
        s = AppSettings(code_strategies_enabled=True)
        assert s.code_strategies_enabled is True


class TestCodeStrategyPromptSuffix:
    def test_suffix_contains_code_strategy_mode(self) -> None:
        from autocontext.prompts.templates import code_strategy_competitor_suffix

        suffix = code_strategy_competitor_suffix("Return {aggression, defense}")
        assert "CODE STRATEGY MODE" in suffix
        assert "result" in suffix

    def test_suffix_includes_strategy_interface(self) -> None:
        from autocontext.prompts.templates import code_strategy_competitor_suffix

        suffix = code_strategy_competitor_suffix("aggression: float 0-1")
        assert "aggression: float 0-1" in suffix

    def test_suffix_mentions_get_observation(self) -> None:
        from autocontext.prompts.templates import code_strategy_competitor_suffix

        suffix = code_strategy_competitor_suffix("")
        assert "get_observation" in suffix


class TestDeterministicCodeStrategy:
    def test_code_strategy_response_contains_python_fence(self) -> None:
        from autocontext.agents.llm_client import DeterministicDevClient

        client = DeterministicDevClient()
        response = client.generate(
            model="test",
            prompt="CODE STRATEGY MODE\nDescribe your strategy",
            max_tokens=800,
            temperature=0.2,
        )
        assert "```python" in response.text
        assert "result" in response.text

    def test_code_strategy_response_for_othello(self) -> None:
        from autocontext.agents.llm_client import DeterministicDevClient

        client = DeterministicDevClient()
        response = client.generate(
            model="test",
            prompt="CODE STRATEGY MODE\n`mobility_weight`\nDescribe your strategy",
            max_tokens=800,
            temperature=0.2,
        )
        assert "```python" in response.text
        assert "mobility_weight" in response.text

    def test_code_strategy_extractable_by_translator(self) -> None:
        """Deterministic code strategy output can be extracted by translate_code()."""
        from autocontext.agents.llm_client import DeterministicDevClient

        client = DeterministicDevClient()
        response = client.generate(
            model="test",
            prompt="CODE STRATEGY MODE\nDescribe your strategy",
            max_tokens=800,
            temperature=0.2,
        )
        translator = StrategyTranslator(MagicMock(), model="test")
        strategy, _ = translator.translate_code(response.text)
        assert "__code__" in strategy
        assert "result" in strategy["__code__"]


class TestMontyCodeStrategyScript:
    def test_code_strategy_script_is_valid_python(self) -> None:
        from autocontext.execution.executors.monty import MontyExecutor

        script = MontyExecutor.build_code_strategy_script("result = {'x': 1}")
        compile(script, "<test>", "exec")

    def test_code_strategy_script_references_extended_externals(self) -> None:
        from autocontext.execution.executors.monty import MontyExecutor

        script = MontyExecutor.build_code_strategy_script("result = {}")
        assert "get_observation" in script
        assert "initial_state" in script
        assert "validate_actions" in script
        assert "step" in script
        assert "is_terminal" in script
        assert "get_result" in script

    def test_code_strategy_script_embeds_agent_code(self) -> None:
        from autocontext.execution.executors.monty import MontyExecutor

        code = "result = {'aggression': 0.8}"
        script = MontyExecutor.build_code_strategy_script(code)
        assert code in script


class TestMontyCodeStrategyDispatch:
    def _make_scenario(self) -> MagicMock:
        from autocontext.scenarios.base import Observation, Result

        scenario = MagicMock()
        scenario.name = "test_scenario"
        scenario.get_observation.return_value = Observation(
            narrative="test narrative",
            state={"resource_density": 0.5},
            constraints=["c1"],
        )
        scenario.initial_state.return_value = {"seed": 42, "terminal": False}
        scenario.validate_actions.return_value = (True, "ok")
        scenario.step.return_value = {"terminal": True, "score": 0.7}
        scenario.is_terminal.return_value = True
        scenario.get_result.return_value = Result(
            score=0.7, winner="challenger", summary="test",
            replay=[], metrics={}, validation_errors=[],
        )
        return scenario

    def test_dispatch_get_observation(self) -> None:
        from autocontext.execution.executors.monty import MontyExecutor

        scenario = self._make_scenario()
        executor = MontyExecutor()
        dispatch = executor._build_code_dispatch(scenario, seed=42)
        result = dispatch("get_observation", ({"seed": 42},))
        assert result["narrative"] == "test narrative"
        assert result["state"]["resource_density"] == 0.5
        assert result["constraints"] == ["c1"]

    def test_dispatch_initial_state_in_code_mode(self) -> None:
        from autocontext.execution.executors.monty import MontyExecutor

        scenario = self._make_scenario()
        executor = MontyExecutor()
        dispatch = executor._build_code_dispatch(scenario, seed=42)
        result = dispatch("initial_state", (42,))
        assert result == {"seed": 42, "terminal": False}

    def test_dispatch_validate_actions_in_code_mode(self) -> None:
        from autocontext.execution.executors.monty import MontyExecutor

        scenario = self._make_scenario()
        executor = MontyExecutor()
        dispatch = executor._build_code_dispatch(scenario, seed=42)
        result = dispatch("validate_actions", ({"seed": 42}, {"x": 1}))
        assert result == [True, "ok"]

    def test_dispatch_unknown_raises(self) -> None:
        from autocontext.execution.executors.monty import MontyExecutor

        scenario = self._make_scenario()
        executor = MontyExecutor()
        dispatch = executor._build_code_dispatch(scenario, seed=42)
        with pytest.raises(ValueError, match="Unknown external function"):
            dispatch("bogus_function", ())


class TestMontyCodeStrategyExecute:
    def _make_scenario(self) -> MagicMock:
        from autocontext.scenarios.base import Observation, Result

        scenario = MagicMock()
        scenario.name = "test_cs"
        scenario.get_observation.return_value = Observation(
            narrative="n", state={"density": 0.5}, constraints=[],
        )
        scenario.initial_state.return_value = {"seed": 1, "terminal": False}
        scenario.validate_actions.return_value = (True, "ok")
        scenario.step.return_value = {"terminal": True, "score": 0.8, "metrics": {}}
        scenario.is_terminal.return_value = True
        scenario.get_result.return_value = Result(
            score=0.8, winner="challenger", summary="ok",
            replay=[], metrics={}, validation_errors=[],
        )
        scenario.replay_to_narrative.return_value = "replay"
        return scenario

    def _build_success_mock(self) -> MagicMock:
        """Build mock Monty for code strategy with full external call chain."""
        calls = [
            ("initial_state", (1,)),
            ("get_observation", ({"seed": 1, "terminal": False},)),
            ("validate_actions", ({"seed": 1, "terminal": False}, {"aggression": 0.8})),
            ("step", ({"seed": 1, "terminal": False}, {"aggression": 0.8})),
            ("is_terminal", ({"terminal": True, "score": 0.8},)),
            ("get_result", ({"terminal": True, "score": 0.8},)),
        ]
        complete = MagicMock(spec=[])
        complete.output = {
            "score": 0.8, "winner": "challenger", "summary": "ok",
            "replay": [], "metrics": {}, "validation_errors": [],
        }
        snapshots = []
        for fn, args in calls:
            snap = MagicMock()
            snap.function_name = fn
            snap.args = args
            snapshots.append(snap)
        for i, snap in enumerate(snapshots):
            snap.resume.return_value = snapshots[i + 1] if i + 1 < len(snapshots) else complete
        monty = MagicMock()
        monty.start.return_value = snapshots[0]
        return monty

    def test_execute_code_strategy_success(self) -> None:
        from unittest.mock import patch

        from autocontext.execution.executors.monty import MontyExecutor
        from autocontext.scenarios.base import ExecutionLimits

        scenario = self._make_scenario()
        monty_mock = self._build_success_mock()
        executor = MontyExecutor()

        with patch("autocontext.execution.executors.monty._create_monty", return_value=monty_mock):
            result, replay = executor.execute_code_strategy(
                scenario=scenario,
                code="result = {'aggression': 0.8}",
                seed=1,
                limits=ExecutionLimits(),
            )
        assert result.score == 0.8
        assert replay.scenario == "test_cs"

    def test_execute_code_strategy_via_code_key(self) -> None:
        """MontyExecutor.execute() detects __code__ and routes to execute_code_strategy."""
        from unittest.mock import patch

        from autocontext.execution.executors.monty import MontyExecutor
        from autocontext.scenarios.base import ExecutionLimits

        scenario = self._make_scenario()
        monty_mock = self._build_success_mock()
        executor = MontyExecutor()

        with patch("autocontext.execution.executors.monty._create_monty", return_value=monty_mock):
            result, replay = executor.execute(
                scenario=scenario,
                strategy={"__code__": "result = {'aggression': 0.8}"},
                seed=1,
                limits=ExecutionLimits(),
            )
        assert result.score == 0.8

    def test_execute_code_strategy_runtime_error_returns_zero(self) -> None:
        """Code strategy runtime error returns zero-score Result instead of raising."""
        from unittest.mock import patch

        from autocontext.execution.executors.monty import MontyExecutor
        from autocontext.scenarios.base import ExecutionLimits

        scenario = self._make_scenario()
        monty_mock = MagicMock()
        monty_mock.start.side_effect = RuntimeError("bad code")

        executor = MontyExecutor()
        # _create_monty succeeds but monty.start fails
        with patch("autocontext.execution.executors.monty._create_monty", return_value=monty_mock):
            result, replay = executor.execute_code_strategy(
                scenario=scenario,
                code="this is not valid",
                seed=1,
                limits=ExecutionLimits(),
            )
        assert result.score == 0.0
        assert len(result.validation_errors) > 0

    def test_local_executor_detects_code_key(self) -> None:
        """LocalExecutor routes __code__ to MontyExecutor."""
        from unittest.mock import patch

        from autocontext.execution.executors.local import LocalExecutor
        from autocontext.scenarios.base import ExecutionLimits

        scenario = self._make_scenario()
        monty_mock = self._build_success_mock()

        executor = LocalExecutor()
        with patch("autocontext.execution.executors.monty._create_monty", return_value=monty_mock):
            result, replay = executor.execute(
                scenario=scenario,
                strategy={"__code__": "result = {'x': 1}"},
                seed=1,
                limits=ExecutionLimits(),
            )
        assert result.score == 0.8

    def test_regular_strategy_unchanged(self) -> None:
        """MontyExecutor.execute() with normal strategy doesn't route to code path."""
        from unittest.mock import patch

        from autocontext.execution.executors.monty import MontyExecutor
        from autocontext.scenarios.base import ExecutionLimits

        scenario = self._make_scenario()
        # Build a mock for normal parameter strategy execution
        complete = MagicMock(spec=[])
        complete.output = {
            "score": 0.7, "winner": "challenger", "summary": "ok",
            "replay": [], "metrics": {}, "validation_errors": [],
        }
        calls = [
            ("initial_state", (1,)),
            ("validate_actions", ({"seed": 1}, {"aggression": 0.8})),
            ("step", ({"seed": 1}, {"aggression": 0.8})),
            ("is_terminal", ({"terminal": True},)),
            ("get_result", ({"terminal": True},)),
        ]
        snapshots = []
        for fn, args in calls:
            snap = MagicMock()
            snap.function_name = fn
            snap.args = args
            snapshots.append(snap)
        for i, snap in enumerate(snapshots):
            snap.resume.return_value = snapshots[i + 1] if i + 1 < len(snapshots) else complete
        monty_mock = MagicMock()
        monty_mock.start.return_value = snapshots[0]

        executor = MontyExecutor()
        with patch("autocontext.execution.executors.monty._create_monty", return_value=monty_mock):
            result, _ = executor.execute(
                scenario=scenario,
                strategy={"aggression": 0.8},
                seed=1,
                limits=ExecutionLimits(),
            )
        assert result.score == 0.7


class TestOrchestratorCodeRouting:
    def test_code_strategies_calls_translate_code(self) -> None:
        """When code_strategies_enabled, orchestrator calls translate_code()."""
        from unittest.mock import patch

        from autocontext.agents.orchestrator import AgentOrchestrator

        settings = AppSettings(
            agent_provider="deterministic",
            code_strategies_enabled=True,
        )
        orch = AgentOrchestrator.from_settings(settings)

        # Spy on translate_code
        with patch.object(orch.translator, "translate_code", wraps=orch.translator.translate_code) as spy:
            # Build a minimal prompt bundle
            from autocontext.prompts.templates import PromptBundle
            prompts = PromptBundle(
                competitor="CODE STRATEGY MODE\nDescribe your strategy",
                analyst="Analyze strengths/failures",
                coach="You are the playbook coach",
                architect="Propose infrastructure improvements",
            )
            outputs = orch.run_generation(
                prompts,
                generation_index=1,
                strategy_interface="aggression: float",
            )
            spy.assert_called_once()
            assert "__code__" in outputs.strategy

    def test_normal_mode_calls_translate(self) -> None:
        """When code_strategies_enabled=False, orchestrator calls translate()."""
        from unittest.mock import patch

        from autocontext.agents.orchestrator import AgentOrchestrator

        settings = AppSettings(
            agent_provider="deterministic",
            code_strategies_enabled=False,
        )
        orch = AgentOrchestrator.from_settings(settings)

        with patch.object(orch.translator, "translate", wraps=orch.translator.translate) as spy:
            from autocontext.prompts.templates import PromptBundle
            prompts = PromptBundle(
                competitor="Describe your strategy reasoning and recommend specific parameter values.",
                analyst="Analyze strengths/failures",
                coach="You are the playbook coach",
                architect="Propose infrastructure improvements",
            )
            outputs = orch.run_generation(
                prompts,
                generation_index=1,
                strategy_interface="aggression: float 0-1",
            )
            spy.assert_called_once()
            assert "__code__" not in outputs.strategy

    def test_code_strategy_appends_suffix_to_prompt(self) -> None:
        """When code_strategies_enabled, orchestrator appends code strategy suffix."""
        from unittest.mock import patch

        from autocontext.agents.orchestrator import AgentOrchestrator

        settings = AppSettings(
            agent_provider="deterministic",
            code_strategies_enabled=True,
        )
        orch = AgentOrchestrator.from_settings(settings)

        captured_prompts: list[str] = []
        original_run = orch.competitor.run

        def capture_run(prompt: str, **kwargs: object) -> object:
            captured_prompts.append(prompt)
            return original_run(prompt, **kwargs)

        with patch.object(orch.competitor, "run", side_effect=capture_run):
            from autocontext.prompts.templates import PromptBundle
            prompts = PromptBundle(
                competitor="Base prompt",
                analyst="Analyze strengths/failures",
                coach="You are the playbook coach",
                architect="Propose infrastructure improvements",
            )
            orch.run_generation(
                prompts,
                generation_index=1,
                strategy_interface="aggression: float",
            )
        assert len(captured_prompts) == 1
        assert "CODE STRATEGY MODE" in captured_prompts[0]


class TestStageValidationSkip:
    def test_code_strategy_skips_validation_in_stage(self) -> None:
        """stage_agent_generation skips validate_actions for code strategies."""
        from autocontext.agents.orchestrator import AgentOrchestrator
        from autocontext.loop.stage_types import GenerationContext
        from autocontext.loop.stages import stage_agent_generation
        from autocontext.scenarios.base import Observation
        from autocontext.storage import ArtifactStore, SQLiteStore

        settings = AppSettings(
            agent_provider="deterministic",
            code_strategies_enabled=True,
        )
        orch = AgentOrchestrator.from_settings(settings)

        # Mock scenario that would fail validation for code strategies
        scenario = MagicMock()
        scenario.name = "test"
        scenario.initial_state.return_value = {"seed": 1}
        scenario.validate_actions.return_value = (False, "not a parameter dict")
        scenario.get_observation.return_value = Observation(narrative="n", state={}, constraints=[])
        scenario.describe_strategy_interface.return_value = "aggression: float"

        sqlite = MagicMock(spec=SQLiteStore)
        artifacts = MagicMock(spec=ArtifactStore)
        artifacts.persist_tools.return_value = []

        from autocontext.prompts.templates import PromptBundle
        ctx = GenerationContext(
            run_id="test_run",
            scenario_name="test",
            scenario=scenario,
            generation=1,
            settings=settings,
            previous_best=0.0,
            challenger_elo=1000.0,
            score_history=[],
            gate_decision_history=[],
            coach_competitor_hints="",
            replay_narrative="",
        )
        ctx.prompts = PromptBundle(
            competitor="CODE STRATEGY MODE\nDescribe your strategy",
            analyst="Analyze strengths/failures",
            coach="You are the playbook coach",
            architect="Propose infrastructure improvements",
        )
        ctx.tool_context = ""
        ctx.strategy_interface = "aggression: float"

        # This should NOT raise even though validate_actions returns False
        result_ctx = stage_agent_generation(
            ctx, orchestrator=orch, artifacts=artifacts, sqlite=sqlite,
        )
        assert "__code__" in result_ctx.current_strategy
