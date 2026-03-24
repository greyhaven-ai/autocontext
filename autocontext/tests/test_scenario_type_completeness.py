"""Regression test for AC-377: ensure all scenario family types are registered.

Prevents the class of bug where hardcoded allowlists fall behind the
actual type registry. External test scripts should use
get_valid_scenario_types() instead of hardcoded tuples.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from autocontext.openclaw.models import ScenarioInfo
from autocontext.scenarios.families import list_families
from autocontext.scenarios.type_registry import get_valid_scenario_types


class TestScenarioTypeCompleteness:
    """Verify the type registry and downstream consumers stay in sync."""

    def test_registry_matches_registered_family_markers(self) -> None:
        """The helper should derive its allowlist from the family registry."""
        types = get_valid_scenario_types()
        family_markers = {family.scenario_type_marker for family in list_families()}
        assert types == frozenset(family_markers)

    def test_types_are_frozen(self) -> None:
        """Registry returns a frozenset (immutable)."""
        types = get_valid_scenario_types()
        assert isinstance(types, frozenset)

    def test_all_types_are_lowercase_strings(self) -> None:
        """Type names should be lowercase snake_case strings."""
        types = get_valid_scenario_types()
        for t in types:
            assert isinstance(t, str)
            assert t == t.lower(), f"Type '{t}' is not lowercase"
            assert " " not in t, f"Type '{t}' contains spaces"

    def test_all_registry_types_are_accepted_by_scenario_info(self) -> None:
        """A real consumer should accept every registry-derived type."""
        for scenario_type in get_valid_scenario_types():
            info = ScenarioInfo(
                name=f"test_{scenario_type}",
                display_name=f"Test {scenario_type}",
                scenario_type=scenario_type,
                description=f"Scenario of type {scenario_type}",
            )
            assert info.scenario_type == scenario_type

    def test_historical_game_alias_is_not_accepted(self) -> None:
        """Parametric scenarios should not regress back to the old 'game' alias."""
        with pytest.raises(ValidationError):
            ScenarioInfo(
                name="bad_game_alias",
                display_name="Bad Game Alias",
                scenario_type="game",
                description="Old family alias should stay invalid",
            )
