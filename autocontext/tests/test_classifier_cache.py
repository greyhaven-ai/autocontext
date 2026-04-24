"""AC-581 — content-addressable cache for the AC-580 LLM classifier fallback."""
from __future__ import annotations

import json

from autocontext.scenarios.custom.classifier_cache import ClassifierCache
from autocontext.scenarios.custom.family_classifier import (
    FamilyCandidate,
    FamilyClassification,
)

FAMILIES_A = ["agent_task", "simulation", "game"]
FAMILIES_B = ["agent_task", "simulation", "game", "operator_loop"]  # schema change


def _classification(family: str = "simulation", confidence: float = 0.82) -> FamilyClassification:
    return FamilyClassification(
        family_name=family,
        confidence=confidence,
        rationale="mocked rationale",
        alternatives=[
            FamilyCandidate(family_name="agent_task", confidence=0.0, rationale="r"),
        ],
        no_signals_matched=False,
    )


class TestClassifierCacheGetPut:
    def test_get_returns_none_when_file_missing(self, tmp_path) -> None:
        cache = ClassifierCache(tmp_path / "cache.json")
        result = cache.get("some description", FAMILIES_A)
        assert result is None

    def test_put_creates_file_and_get_returns_classification(self, tmp_path) -> None:
        cache = ClassifierCache(tmp_path / "cache.json")
        original = _classification()
        cache.put("please classify me", FAMILIES_A, original)

        fetched = cache.get("please classify me", FAMILIES_A)
        assert fetched is not None
        assert fetched.family_name == original.family_name
        assert fetched.confidence == original.confidence
        assert fetched.rationale == original.rationale

    def test_get_miss_on_different_description(self, tmp_path) -> None:
        cache = ClassifierCache(tmp_path / "cache.json")
        cache.put("description one", FAMILIES_A, _classification())
        assert cache.get("different description", FAMILIES_A) is None

    def test_multiple_entries_coexist(self, tmp_path) -> None:
        cache = ClassifierCache(tmp_path / "cache.json")
        cache.put("desc one", FAMILIES_A, _classification("simulation", 0.8))
        cache.put("desc two", FAMILIES_A, _classification("agent_task", 0.6))

        assert cache.get("desc one", FAMILIES_A).family_name == "simulation"
        assert cache.get("desc two", FAMILIES_A).family_name == "agent_task"


class TestClassifierCacheSchemaInvalidation:
    """When the registered family set changes, the cache is considered invalid."""

    def test_get_returns_none_when_registry_changed(self, tmp_path) -> None:
        cache = ClassifierCache(tmp_path / "cache.json")
        cache.put("same description", FAMILIES_A, _classification())

        # Different family list → schema mismatch → miss (don't return stale data).
        assert cache.get("same description", FAMILIES_B) is None

    def test_put_with_new_schema_overwrites_stale_entries(self, tmp_path) -> None:
        path = tmp_path / "cache.json"
        cache = ClassifierCache(path)

        # Seed with old-schema data.
        cache.put("shared description", FAMILIES_A, _classification("simulation", 0.8))

        # Write under a new schema.
        cache.put("shared description", FAMILIES_B, _classification("operator_loop", 0.9))

        # Old schema entries are gone; new schema read works.
        assert cache.get("shared description", FAMILIES_A) is None
        assert cache.get("shared description", FAMILIES_B).family_name == "operator_loop"

    def test_registered_family_order_does_not_affect_schema_version(self, tmp_path) -> None:
        # Order of list_families() should not invalidate the cache.
        cache = ClassifierCache(tmp_path / "cache.json")
        cache.put("desc", FAMILIES_A, _classification())

        reordered = list(reversed(FAMILIES_A))
        assert cache.get("desc", reordered) is not None


class TestClassifierCacheRobustness:
    def test_corrupt_json_returns_none_without_raising(self, tmp_path) -> None:
        path = tmp_path / "cache.json"
        path.write_text("{not valid json", encoding="utf-8")

        cache = ClassifierCache(path)
        assert cache.get("anything", FAMILIES_A) is None

    def test_corrupt_file_is_overwritten_by_put(self, tmp_path) -> None:
        path = tmp_path / "cache.json"
        path.write_text("{not valid json", encoding="utf-8")

        cache = ClassifierCache(path)
        cache.put("desc", FAMILIES_A, _classification())

        assert cache.get("desc", FAMILIES_A) is not None

    def test_put_creates_parent_directory(self, tmp_path) -> None:
        # Deep path whose parent directory doesn't exist.
        path = tmp_path / "nested" / "dirs" / "cache.json"
        cache = ClassifierCache(path)
        cache.put("desc", FAMILIES_A, _classification())
        assert path.exists()
        assert cache.get("desc", FAMILIES_A) is not None

    def test_file_format_is_json_with_schema_version_and_entries(self, tmp_path) -> None:
        path = tmp_path / "cache.json"
        cache = ClassifierCache(path)
        cache.put("desc", FAMILIES_A, _classification())

        data = json.loads(path.read_text(encoding="utf-8"))
        assert "schema_version" in data
        assert "entries" in data
        assert isinstance(data["entries"], dict)
        # Entries are keyed by opaque content hashes — not the raw description.
        assert "desc" not in data["entries"]


class TestLlmFallbackCacheIntegration:
    """AC-580 fallback consults the cache when provided and writes back on success."""

    @staticmethod
    def _gibberish() -> str:
        return "xyz zzz qqq no keyword signals"

    def test_cache_miss_invokes_llm_and_writes_cache(self, tmp_path) -> None:
        from autocontext.scenarios.custom.family_classifier import classify_scenario_family

        cache = ClassifierCache(tmp_path / "cache.json")
        call_count = {"n": 0}

        def stub_llm(system: str, user: str) -> str:
            del system, user
            call_count["n"] += 1
            return '{"family": "simulation", "confidence": 0.82, "rationale": "mocked"}'

        result = classify_scenario_family(self._gibberish(), llm_fn=stub_llm, cache=cache)
        assert result.family_name == "simulation"
        assert call_count["n"] == 1

        # Next call with same description should hit the cache.
        result2 = classify_scenario_family(self._gibberish(), llm_fn=stub_llm, cache=cache)
        assert result2.family_name == "simulation"
        assert call_count["n"] == 1  # LLM not called again

    def test_cache_none_means_no_caching(self, tmp_path) -> None:
        # Regression guard: existing callers pass no cache and still work.
        from autocontext.scenarios.custom.family_classifier import classify_scenario_family

        call_count = {"n": 0}

        def stub_llm(system: str, user: str) -> str:
            del system, user
            call_count["n"] += 1
            return '{"family": "simulation", "confidence": 0.82, "rationale": "mocked"}'

        classify_scenario_family(self._gibberish(), llm_fn=stub_llm)
        classify_scenario_family(self._gibberish(), llm_fn=stub_llm)
        assert call_count["n"] == 2  # Both calls went to LLM

    def test_llm_failure_is_not_cached(self, tmp_path) -> None:
        # Negative results (LLM raised / parse failed) must not be written —
        # otherwise a transient provider hiccup would poison future lookups.
        from autocontext.scenarios.custom.family_classifier import classify_scenario_family

        cache = ClassifierCache(tmp_path / "cache.json")

        def bad_llm(system: str, user: str) -> str:
            del system, user
            return "not json at all"

        result = classify_scenario_family(self._gibberish(), llm_fn=bad_llm, cache=cache)
        # Fallback failed → keyword fallback returned.
        assert result.no_signals_matched is True

        # Cache file should be empty (or non-existent) — no entries written.
        cache_path = tmp_path / "cache.json"
        if cache_path.exists():
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            assert data.get("entries", {}) == {}

    def test_cache_hit_preserves_all_fields(self, tmp_path) -> None:
        from autocontext.scenarios.custom.family_classifier import classify_scenario_family

        cache = ClassifierCache(tmp_path / "cache.json")

        def stub_llm(system: str, user: str) -> str:
            del system, user
            return '{"family": "simulation", "confidence": 0.82, "rationale": "first call rationale"}'

        first = classify_scenario_family(self._gibberish(), llm_fn=stub_llm, cache=cache)

        def forbidden_llm(system: str, user: str) -> str:
            raise AssertionError("cache hit should prevent LLM call")

        second = classify_scenario_family(self._gibberish(), llm_fn=forbidden_llm, cache=cache)
        assert second.family_name == first.family_name
        assert second.confidence == first.confidence
        assert second.rationale == first.rationale
        assert second.no_signals_matched is False

    def test_solve_and_new_scenario_share_live_classifier_cache(self, tmp_path, monkeypatch) -> None:
        from autocontext.agents.llm_client import DeterministicDevClient
        from autocontext.agents.subagent_runtime import SubagentRuntime
        from autocontext.knowledge.solver import SolveScenarioBuilder
        from autocontext.scenarios.custom.agent_task_creator import AgentTaskCreator
        from autocontext.scenarios.custom.classifier_cache import default_classifier_cache_path

        llm_calls = {"count": 0}

        def stub_llm(system: str, user: str) -> str:
            del system, user
            llm_calls["count"] += 1
            return '{"family": "simulation", "confidence": 0.82, "rationale": "mocked"}'

        description = (
            "## Scenario Proposal\n\n"
            "**Priority:** Week 4\n\n"
            "### Description\n\n"
            "xyz zzz qqq nonsense gibberish for a hidden family "
            "(e.g., an essay-quality metric that rewards length).\n\n"
            "## Implementation Guidance\n\n"
            "This section should not affect family classification."
        )

        runtime = SubagentRuntime(DeterministicDevClient())
        builder = SolveScenarioBuilder(
            runtime=runtime,
            llm_fn=stub_llm,
            model="test-model",
            knowledge_root=tmp_path,
        )

        class _BuiltScenario:
            name = "cached_solver_fixture"

        def _fake_builder_create(self, description: str, *, family_name: str = "") -> _BuiltScenario:
            del self, description
            assert family_name == "simulation"
            return _BuiltScenario()

        with monkeypatch.context() as m:
            m.setattr(
                "autocontext.scenarios.custom.agent_task_creator.AgentTaskCreator.create",
                _fake_builder_create,
            )
            result = builder.build(description)

        assert result.family_name == "simulation"
        assert result.llm_classifier_fallback_used is True
        assert llm_calls["count"] == 1
        assert default_classifier_cache_path(tmp_path).exists()

        class _FakeFamilyCreator:
            def create(self, description: str, *, name: str):
                del description, name
                return object()

        def _fake_create_for_family(family_name: str, llm_fn, knowledge_root):
            del llm_fn, knowledge_root
            assert family_name == "simulation"
            return _FakeFamilyCreator()

        monkeypatch.setattr(
            "autocontext.scenarios.custom.agent_task_creator.create_for_family",
            _fake_create_for_family,
        )

        creator = AgentTaskCreator(llm_fn=stub_llm, knowledge_root=tmp_path)
        creator.create(description)

        assert llm_calls["count"] == 1
