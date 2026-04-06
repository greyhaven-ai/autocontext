"""Track C — AC-524: detect_family must prefer an explicit `family` class attribute
over structural isinstance probing.

Custom-generated scenarios from the generic ScenarioCreator extend ScenarioInterface
(the game base class) even when the designer classified the request as operator_loop.
This causes detect_family to report "game" — the wrong family.

The fix: detect_family checks `getattr(scenario, "family", None)` first, then
falls back to isinstance for legacy scenarios.
"""

from __future__ import annotations

from autocontext.scenarios.families import detect_family

# ---------------------------------------------------------------------------
# Helpers — build minimal mock scenarios
# ---------------------------------------------------------------------------


class MockGameScenario:
    """Extends nothing; no family attribute. isinstance will match ScenarioInterface
    only if it actually inherits from it."""

    pass


class MockOperatorLoopViaAttribute:
    """A scenario with an explicit family attribute but NO inheritance from
    OperatorLoopInterface. This simulates a custom-generated scenario that
    went through the generic ScenarioCreator codegen path."""

    family = "operator_loop"


class MockSimulationViaAttribute:
    """Same pattern for simulation family."""

    family = "simulation"


class MockBogusFamily:
    """Explicit family attribute with a name that isn't registered."""

    family = "bogus_nonexistent_family"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDetectFamilyExplicitAttribute:
    """detect_family should check the explicit `family` attribute first."""

    def test_returns_operator_loop_family_from_attribute(self) -> None:
        """Core AC-524 fix: a custom scenario with `family = "operator_loop"`
        attribute must be detected as operator_loop even if it doesn't inherit
        from OperatorLoopInterface."""
        scenario = MockOperatorLoopViaAttribute()
        result = detect_family(scenario)
        assert result is not None
        assert result.name == "operator_loop"

    def test_returns_simulation_family_from_attribute(self) -> None:
        """Same pattern for a different family."""
        scenario = MockSimulationViaAttribute()
        result = detect_family(scenario)
        assert result is not None
        assert result.name == "simulation"

    def test_falls_back_to_structural_when_no_attribute(self) -> None:
        """Legacy scenario without `family` attribute still works via isinstance."""
        from autocontext.scenarios.base import ScenarioInterface

        class LegacyGame(ScenarioInterface):
            def describe_rules(self) -> str:
                return "test"

            def describe_strategy_interface(self) -> str:
                return "test"

            def describe_evaluation_criteria(self) -> str:
                return "test"

            def initial_state(self, seed: int = 0) -> dict:
                return {}

            def get_observation(self, state: dict, player: int) -> dict:
                return {}

            def validate_actions(self, state: dict, actions: list[dict]) -> tuple[bool, str]:
                return True, ""

            def step(self, state: dict, actions: list[dict]) -> dict:
                return {}

            def is_terminal(self, state: dict) -> bool:
                return True

            def get_result(self, state: dict) -> dict:
                return {}

            def replay_to_narrative(self, replay: list[dict]) -> str:
                return ""

            def render_frame(self, state: dict) -> str:
                return ""

        result = detect_family(LegacyGame())
        assert result is not None
        assert result.name == "game"

    def test_falls_back_to_structural_when_family_attribute_not_registered(self) -> None:
        """A bogus family name in the attribute does not crash — it falls
        through to structural probing. If structural also fails, returns None."""
        scenario = MockBogusFamily()
        result = detect_family(scenario)
        # MockBogusFamily doesn't inherit from any registered interface, so
        # structural probing also returns None.
        assert result is None

    def test_attribute_takes_precedence_over_structural(self) -> None:
        """If a scenario both inherits from ScenarioInterface (game) AND has
        `family = "operator_loop"`, the explicit attribute wins."""
        from autocontext.scenarios.base import ScenarioInterface

        class HybridScenario(ScenarioInterface):
            family = "operator_loop"

            def describe_rules(self) -> str:
                return "test"

            def describe_strategy_interface(self) -> str:
                return "test"

            def describe_evaluation_criteria(self) -> str:
                return "test"

            def initial_state(self, seed: int = 0) -> dict:
                return {}

            def get_observation(self, state: dict, player: int) -> dict:
                return {}

            def validate_actions(self, state: dict, actions: list[dict]) -> tuple[bool, str]:
                return True, ""

            def step(self, state: dict, actions: list[dict]) -> dict:
                return {}

            def is_terminal(self, state: dict) -> bool:
                return True

            def get_result(self, state: dict) -> dict:
                return {}

            def replay_to_narrative(self, replay: list[dict]) -> str:
                return ""

            def render_frame(self, state: dict) -> str:
                return ""

        result = detect_family(HybridScenario())
        assert result is not None
        assert result.name == "operator_loop", "Explicit family attribute must take precedence over isinstance-based detection"


class TestCodegenEmitsFamilyAttribute:
    """The generic codegen must emit a `family` class attribute when the spec
    carries one, so detect_family works for all custom-generated scenarios."""

    def test_generic_codegen_includes_family_attribute_when_set(self) -> None:
        from autocontext.scenarios.custom.codegen import generate_scenario_class
        from autocontext.scenarios.custom.spec import ScenarioSpec

        spec = ScenarioSpec(
            name="test_op_loop",
            display_name="Test Op Loop",
            description="Test scenario",
            strategy_interface_description="test",
            evaluation_criteria="test",
        )
        # Inject family if the field exists; the fix adds it.
        if hasattr(spec, "family"):
            spec.family = "operator_loop"  # type: ignore[attr-defined]

        source = generate_scenario_class(spec)
        assert 'family = "operator_loop"' in source, "Generated class must include family attribute when spec.family is set"

    def test_generic_codegen_omits_family_attribute_when_not_set(self) -> None:
        """Backward compat: specs without an explicit family should NOT get
        a family attribute (let detect_family fall through to structural)."""
        from autocontext.scenarios.custom.codegen import generate_scenario_class
        from autocontext.scenarios.custom.spec import ScenarioSpec

        spec = ScenarioSpec(
            name="test_plain",
            display_name="Test Plain",
            description="Test scenario",
            strategy_interface_description="test",
            evaluation_criteria="test",
        )
        source = generate_scenario_class(spec)
        # Should NOT contain an explicit family attribute
        assert "family =" not in source or 'family = ""' in source or "family = None" in source
