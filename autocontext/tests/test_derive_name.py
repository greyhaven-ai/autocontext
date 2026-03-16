"""Tests for AC-285: improved derive_name with determinism and aliasing.

Covers: derive_name, derive_name_legacy, resolve_alias, build_alias_map.
"""

from __future__ import annotations

# ===========================================================================
# derive_name — improved algorithm
# ===========================================================================


class TestDeriveName:
    def test_domain_nouns_preferred_over_adjectives(self) -> None:
        """Should pick 'drug', 'interaction', 'prediction' over 'appropriateness'."""
        from autocontext.scenarios.custom.naming import derive_name

        name = derive_name("Create an agent task for drug interaction prediction and safety appropriateness evaluation")
        words = name.split("_")
        # Domain nouns should appear; abstract adjectives should not dominate
        assert "drug" in words or "interaction" in words or "prediction" in words

    def test_clinical_trial_example(self) -> None:
        from autocontext.scenarios.custom.naming import derive_name

        name = derive_name(
            "Design a clinical trial protocol for a randomized controlled "
            "study of demographics-aware treatment appropriateness"
        )
        words = name.split("_")
        assert "clinical" in words or "trial" in words or "protocol" in words

    def test_wargame_example(self) -> None:
        from autocontext.scenarios.custom.naming import derive_name

        name = derive_name("Create a geopolitical crisis wargame scenario with escalation dynamics")
        words = name.split("_")
        assert "geopolitical" in words or "wargame" in words or "crisis" in words

    def test_deterministic(self) -> None:
        """Same input always produces same output."""
        from autocontext.scenarios.custom.naming import derive_name

        desc = "Analyze drug interaction patterns for clinical safety review"
        assert derive_name(desc) == derive_name(desc)

    def test_different_inputs_different_names(self) -> None:
        from autocontext.scenarios.custom.naming import derive_name

        n1 = derive_name("Drug interaction prediction task")
        n2 = derive_name("Climate change policy analysis task")
        assert n1 != n2

    def test_empty_description(self) -> None:
        from autocontext.scenarios.custom.naming import derive_name

        assert derive_name("") == "custom"
        assert derive_name("   ") == "custom"

    def test_only_stop_words(self) -> None:
        from autocontext.scenarios.custom.naming import derive_name

        assert derive_name("create a task for the agent") == "custom"

    def test_short_description(self) -> None:
        from autocontext.scenarios.custom.naming import derive_name

        name = derive_name("Sort list")
        assert name == "sort_list" or "sort" in name

    def test_max_three_words(self) -> None:
        from autocontext.scenarios.custom.naming import derive_name

        name = derive_name("Advanced quantum computing simulation for molecular dynamics research")
        words = name.split("_")
        assert len(words) <= 3

    def test_valid_identifier(self) -> None:
        """Name should be a valid Python/filesystem identifier."""
        from autocontext.scenarios.custom.naming import derive_name

        name = derive_name("Test with special chars: é, ñ, ü!")
        assert name.replace("_", "").isalnum()


# ===========================================================================
# derive_name_legacy — backward compat
# ===========================================================================


class TestDeriveNameLegacy:
    def test_matches_current_behavior(self) -> None:
        """Legacy function should produce same output as the old algorithm."""
        from autocontext.scenarios.custom.naming import derive_name_legacy

        # The old algorithm: sort by length descending, take top 3
        name = derive_name_legacy(
            "Create an agent task for drug interaction prediction and safety appropriateness evaluation"
        )
        # Old behavior: longest words first → "appropriateness" would dominate
        words = name.split("_")
        assert len(words) <= 3
        # The longest non-stop word is "appropriateness" (15 chars)
        assert words[0] == "appropriateness"

    def test_deterministic(self) -> None:
        from autocontext.scenarios.custom.naming import derive_name_legacy

        desc = "Some complex description here"
        assert derive_name_legacy(desc) == derive_name_legacy(desc)


# ===========================================================================
# resolve_alias
# ===========================================================================


class TestResolveAlias:
    def test_alias_found(self) -> None:
        from autocontext.scenarios.custom.naming import resolve_alias

        aliases = {"old_name": "new_name", "another_old": "another_new"}
        assert resolve_alias("old_name", aliases) == "new_name"

    def test_no_alias_returns_original(self) -> None:
        from autocontext.scenarios.custom.naming import resolve_alias

        aliases = {"old_name": "new_name"}
        assert resolve_alias("unknown", aliases) == "unknown"

    def test_empty_aliases(self) -> None:
        from autocontext.scenarios.custom.naming import resolve_alias

        assert resolve_alias("any_name", {}) == "any_name"


# ===========================================================================
# build_alias_map
# ===========================================================================


class TestBuildAliasMap:
    def test_builds_mapping_when_names_differ(self) -> None:
        from autocontext.scenarios.custom.naming import (
            build_alias_map,
            derive_name,
            derive_name_legacy,
        )

        descriptions = [
            "Create an agent task for drug interaction prediction and safety appropriateness evaluation",
            "Design a clinical trial protocol for randomized controlled study",
        ]
        aliases = build_alias_map(descriptions, derive_name_legacy, derive_name)

        # Should map old→new only where they differ
        for desc in descriptions:
            old = derive_name_legacy(desc)
            new = derive_name(desc)
            if old != new:
                assert aliases[old] == new

    def test_no_aliases_when_names_match(self) -> None:
        from autocontext.scenarios.custom.naming import build_alias_map, derive_name

        descriptions = ["sort list", "hello world"]
        aliases = build_alias_map(descriptions, derive_name, derive_name)
        assert len(aliases) == 0  # Same function → no aliases needed

    def test_empty_descriptions(self) -> None:
        from autocontext.scenarios.custom.naming import (
            build_alias_map,
            derive_name,
            derive_name_legacy,
        )

        assert build_alias_map([], derive_name_legacy, derive_name) == {}
