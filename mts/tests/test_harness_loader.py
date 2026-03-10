"""Tests for HarnessLoader, parse_architect_harness_specs, and ArtifactStore harness methods."""
from __future__ import annotations

import textwrap
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from mts.agents.architect import parse_architect_harness_specs
from mts.execution.harness_loader import _SAFE_BUILTINS, HarnessLoader, HarnessValidationResult
from mts.storage.artifacts import ArtifactStore

# ── HarnessLoader ──────────────────────────────────────────────────────────────


class TestHarnessLoaderLoadEmpty:
    def test_empty_dir(self, tmp_path: Path) -> None:
        h_dir = tmp_path / "harness"
        h_dir.mkdir()
        loader = HarnessLoader(h_dir)
        loaded = loader.load()
        assert loaded == []

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        loader = HarnessLoader(tmp_path / "no_such_dir")
        loaded = loader.load()
        assert loaded == []


class TestHarnessLoaderLoadValid:
    def _write_validator(self, harness_dir: Path, name: str, code: str) -> None:
        harness_dir.mkdir(parents=True, exist_ok=True)
        (harness_dir / f"{name}.py").write_text(textwrap.dedent(code), encoding="utf-8")

    def test_load_single_validator(self, tmp_path: Path) -> None:
        h_dir = tmp_path / "harness"
        self._write_validator(h_dir, "check_moves", """
            def validate_strategy(strategy, scenario):
                if "moves" not in strategy:
                    return False, ["missing 'moves' key"]
                return True, []
        """)
        loader = HarnessLoader(h_dir)
        loaded = loader.load()
        assert loaded == ["check_moves"]
        assert loader.has_callable("check_moves", "validate_strategy")

    def test_load_multiple_files(self, tmp_path: Path) -> None:
        h_dir = tmp_path / "harness"
        self._write_validator(h_dir, "alpha", """
            def validate_strategy(strategy, scenario):
                return True, []
        """)
        self._write_validator(h_dir, "beta", """
            def validate_strategy(strategy, scenario):
                return True, []
            def enumerate_legal_actions(state):
                return []
        """)
        loader = HarnessLoader(h_dir)
        loaded = loader.load()
        assert sorted(loaded) == ["alpha", "beta"]
        assert loader.has_callable("beta", "enumerate_legal_actions")

    def test_skip_syntax_error(self, tmp_path: Path) -> None:
        h_dir = tmp_path / "harness"
        h_dir.mkdir()
        (h_dir / "bad.py").write_text("def validate_strategy(:\n", encoding="utf-8")
        self._write_validator(h_dir, "good", """
            def validate_strategy(strategy, scenario):
                return True, []
        """)
        loader = HarnessLoader(h_dir)
        loaded = loader.load()
        assert loaded == ["good"]

    def test_skip_missing_validate_strategy(self, tmp_path: Path) -> None:
        h_dir = tmp_path / "harness"
        h_dir.mkdir()
        (h_dir / "helpers.py").write_text("def helper(): pass\n", encoding="utf-8")
        loader = HarnessLoader(h_dir)
        loaded = loader.load()
        # File is loaded (callables tracked) but no validator registered
        assert loaded == ["helpers"]
        assert not loader.has_callable("helpers", "validate_strategy")


class TestHarnessLoaderValidation:
    def _make_loader_with_validator(self, tmp_path: Path, code: str) -> HarnessLoader:
        h_dir = tmp_path / "harness"
        h_dir.mkdir(parents=True, exist_ok=True)
        (h_dir / "v.py").write_text(textwrap.dedent(code), encoding="utf-8")
        loader = HarnessLoader(h_dir)
        loader.load()
        return loader

    def test_validation_passes(self, tmp_path: Path) -> None:
        loader = self._make_loader_with_validator(tmp_path, """
            def validate_strategy(strategy, scenario):
                return True, []
        """)
        result = loader.validate_strategy({"moves": []}, None)
        assert result.passed
        assert result.errors == []

    def test_validation_fails(self, tmp_path: Path) -> None:
        loader = self._make_loader_with_validator(tmp_path, """
            def validate_strategy(strategy, scenario):
                return False, ["bad move"]
        """)
        result = loader.validate_strategy({}, None)
        assert not result.passed
        assert any("bad move" in e for e in result.errors)

    def test_validator_exception_captured(self, tmp_path: Path) -> None:
        loader = self._make_loader_with_validator(tmp_path, """
            def validate_strategy(strategy, scenario):
                return 1/0, []
        """)
        result = loader.validate_strategy({}, None)
        assert not result.passed
        assert any("exception" in e for e in result.errors)

    def test_empty_validators_passes(self, tmp_path: Path) -> None:
        h_dir = tmp_path / "harness"
        h_dir.mkdir()
        loader = HarnessLoader(h_dir)
        loader.load()
        result = loader.validate_strategy({}, None)
        assert result.passed

    def test_get_callable(self, tmp_path: Path) -> None:
        loader = self._make_loader_with_validator(tmp_path, """
            def validate_strategy(strategy, scenario):
                return True, []
            def parse_game_state(raw):
                return {"parsed": True}
        """)
        fn = loader.get_callable("v", "parse_game_state")
        assert fn is not None
        assert fn("raw") == {"parsed": True}

    def test_get_callable_missing(self, tmp_path: Path) -> None:
        loader = self._make_loader_with_validator(tmp_path, """
            def validate_strategy(strategy, scenario):
                return True, []
        """)
        assert loader.get_callable("v", "nonexistent") is None
        assert loader.get_callable("missing_file", "validate_strategy") is None


# ── parse_architect_harness_specs ──────────────────────────────────────────────


class TestParseArchitectHarnessSpecs:
    def test_valid_harness_json(self) -> None:
        content = (
            "Some text\n"
            "<!-- HARNESS_START -->\n"
            '{"harness": [{"name": "check", "code": "def validate_strategy(s, sc):\\n    return True, []"}]}\n'
            "<!-- HARNESS_END -->\n"
            "More text"
        )
        specs = parse_architect_harness_specs(content)
        assert len(specs) == 1
        assert specs[0]["name"] == "check"
        assert "validate_strategy" in specs[0]["code"]

    def test_no_markers(self) -> None:
        assert parse_architect_harness_specs("no markers here") == []

    def test_invalid_json(self) -> None:
        content = "<!-- HARNESS_START -->\nnot json\n<!-- HARNESS_END -->"
        assert parse_architect_harness_specs(content) == []

    def test_missing_fields(self) -> None:
        content = (
            "<!-- HARNESS_START -->\n"
            '{"harness": [{"name": "no_code"}]}\n'
            "<!-- HARNESS_END -->"
        )
        assert parse_architect_harness_specs(content) == []

    def test_syntax_error_in_code(self) -> None:
        content = (
            "<!-- HARNESS_START -->\n"
            '{"harness": [{"name": "bad", "code": "def f(:\\n"}]}\n'
            "<!-- HARNESS_END -->"
        )
        assert parse_architect_harness_specs(content) == []

    def test_mixed_valid_invalid(self) -> None:
        content = (
            "<!-- HARNESS_START -->\n"
            '{"harness": ['
            '{"name": "good", "code": "x = 1"},'
            '{"name": "bad", "code": "def f(:\\n"}'
            "]}\n"
            "<!-- HARNESS_END -->"
        )
        specs = parse_architect_harness_specs(content)
        assert len(specs) == 1
        assert specs[0]["name"] == "good"

    def test_description_included(self) -> None:
        content = (
            "<!-- HARNESS_START -->\n"
            '{"harness": [{"name": "v", "description": "A validator", "code": "x = 1"}]}\n'
            "<!-- HARNESS_END -->"
        )
        specs = parse_architect_harness_specs(content)
        assert specs[0].get("description") == "A validator"


# ── ArtifactStore harness methods ──────────────────────────────────────────────


class TestArtifactStoreHarness:
    def _make_store(self, tmp_path: Path) -> ArtifactStore:
        knowledge = tmp_path / "knowledge"
        knowledge.mkdir()
        skills = tmp_path / "skills"
        skills.mkdir()
        runs = tmp_path / "runs"
        runs.mkdir()
        claude_skills = tmp_path / "claude_skills"
        claude_skills.mkdir()
        return ArtifactStore(
            runs_root=runs,
            knowledge_root=knowledge,
            skills_root=skills,
            claude_skills_path=claude_skills,
        )

    def test_harness_dir(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        h_dir = store.harness_dir("test_scenario")
        assert h_dir == tmp_path / "knowledge" / "test_scenario" / "harness"

    def test_persist_harness_creates_files(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        specs = [{"name": "check_moves", "code": "def validate_strategy(s, sc):\n    return True, []"}]
        created = store.persist_harness("test_scenario", 1, specs)
        assert any("check_moves" in c for c in created)
        h_dir = store.harness_dir("test_scenario")
        assert (h_dir / "check_moves.py").exists()

    def test_persist_harness_archives_old_version(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        specs_v1 = [{"name": "v", "code": "x = 1"}]
        store.persist_harness("test_scenario", 1, specs_v1)
        specs_v2 = [{"name": "v", "code": "x = 2"}]
        store.persist_harness("test_scenario", 2, specs_v2)
        archive = store.harness_dir("test_scenario") / "_archive"
        assert archive.exists()
        archived = list(archive.glob("v_gen*.py"))
        assert len(archived) == 1

    def test_persist_harness_skips_syntax_error(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        specs = [{"name": "bad", "code": "def f(:\n"}]
        created = store.persist_harness("test_scenario", 1, specs)
        assert created == []

    def test_read_harness_context(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        specs = [{"name": "v", "code": "def validate_strategy(s, sc):\n    return True, []"}]
        store.persist_harness("test_scenario", 1, specs)
        context = store.read_harness_context("test_scenario")
        assert "v.py" in context
        assert "validate_strategy" in context

    def test_read_harness_context_empty(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        context = store.read_harness_context("test_scenario")
        assert "No harness" in context


# ── stage_prevalidation with harness ───────────────────────────────────────────


class TestStagePrevalidationHarness:
    def _make_ctx(self, tmp_path: Path, *, prevalidation_enabled: bool = True) -> Any:
        from mts.config.settings import AppSettings
        from mts.loop.stage_types import GenerationContext

        settings = AppSettings(
            prevalidation_enabled=prevalidation_enabled,
            harness_validators_enabled=True,
        )
        scenario = MagicMock()
        scenario.initial_state.return_value = {}
        scenario.execute_match.return_value = (1.0, {})
        return GenerationContext(
            run_id="test",
            generation=1,
            scenario_name="test",
            scenario=scenario,
            settings=settings,
            current_strategy={"moves": ["up"]},
            previous_best=0.0,
            challenger_elo=1000.0,
            score_history=[],
            gate_decision_history=[],
            coach_competitor_hints="",
            replay_narrative="",
        )

    def test_harness_disabled_skips(self, tmp_path: Path) -> None:
        from mts.loop.stage_prevalidation import stage_prevalidation

        ctx = self._make_ctx(tmp_path, prevalidation_enabled=False)
        events = MagicMock()
        agents = MagicMock()

        stage_prevalidation(ctx, events=events, agents=agents, harness_loader=None)
        events.emit.assert_not_called()

    def test_harness_none_skips_harness_phase(self, tmp_path: Path) -> None:
        from mts.loop.stage_prevalidation import stage_prevalidation

        ctx = self._make_ctx(tmp_path)
        events = MagicMock()
        agents = MagicMock()

        # With harness_loader=None, should go straight to self-play
        # Mock the StrategyValidator to pass immediately
        with pytest.MonkeyPatch.context() as mp:
            mock_validator = MagicMock()
            mock_validator.validate.return_value = MagicMock(passed=True, errors=[])
            mp.setattr("mts.loop.stage_prevalidation.StrategyValidator", lambda *a, **kw: mock_validator)

            stage_prevalidation(ctx, events=events, agents=agents, harness_loader=None)

        # Should have emitted dry_run_started (no harness events)
        event_names = [call[0][0] for call in events.emit.call_args_list]
        assert "harness_validation_failed" not in event_names

    def test_harness_passes(self, tmp_path: Path) -> None:
        from mts.loop.stage_prevalidation import stage_prevalidation

        ctx = self._make_ctx(tmp_path)
        events = MagicMock()
        agents = MagicMock()

        harness_loader = MagicMock()
        harness_loader.validate_strategy.return_value = HarnessValidationResult(passed=True, errors=[])

        with pytest.MonkeyPatch.context() as mp:
            mock_validator = MagicMock()
            mock_validator.validate.return_value = MagicMock(passed=True, errors=[])
            mp.setattr("mts.loop.stage_prevalidation.StrategyValidator", lambda *a, **kw: mock_validator)

            stage_prevalidation(ctx, events=events, agents=agents, harness_loader=harness_loader)

        event_names = [call[0][0] for call in events.emit.call_args_list]
        assert "harness_validation_failed" not in event_names

    def test_harness_fails_emits_event(self, tmp_path: Path) -> None:
        from mts.loop.stage_prevalidation import stage_prevalidation

        ctx = self._make_ctx(tmp_path)
        events = MagicMock()
        agents = MagicMock()
        agents.competitor.revise.side_effect = Exception("no revision")

        harness_loader = MagicMock()
        harness_loader.validate_strategy.return_value = HarnessValidationResult(
            passed=False, errors=["invalid move"],
        )

        with pytest.MonkeyPatch.context() as mp:
            mock_validator = MagicMock()
            mock_validator.validate.return_value = MagicMock(passed=True, errors=[])
            mp.setattr("mts.loop.stage_prevalidation.StrategyValidator", lambda *a, **kw: mock_validator)

            stage_prevalidation(ctx, events=events, agents=agents, harness_loader=harness_loader)

        event_names = [call[0][0] for call in events.emit.call_args_list]
        assert "harness_validation_failed" in event_names


# ── Sandbox hardening ──────────────────────────────────────────────────────────


class TestHarnessLoaderSandbox:
    """Tests for sandbox hardening: AST safety checks, timeout, builtins restrictions."""

    def _write_and_load(self, tmp_path: Path, code: str, *, timeout: float = 5.0) -> tuple[HarnessLoader, list[str]]:
        h_dir = tmp_path / "harness"
        h_dir.mkdir(parents=True, exist_ok=True)
        (h_dir / "test.py").write_text(textwrap.dedent(code), encoding="utf-8")
        loader = HarnessLoader(h_dir, timeout_seconds=timeout)
        loaded = loader.load()
        return loader, loaded

    def test_import_rejected(self, tmp_path: Path) -> None:
        _, loaded = self._write_and_load(tmp_path, """\
            import os
            def validate_strategy(s, sc):
                return True, []
        """)
        assert loaded == []

    def test_class_hierarchy_traversal_rejected(self, tmp_path: Path) -> None:
        _, loaded = self._write_and_load(tmp_path, """\
            def validate_strategy(s, sc):
                x = ().__class__.__bases__[0].__subclasses__()
                return True, []
        """)
        assert loaded == []

    def test_eval_rejected(self, tmp_path: Path) -> None:
        # Tests that code using the dangerous 'eval' builtin is rejected by AST check
        _, loaded = self._write_and_load(tmp_path, """\
            def validate_strategy(s, sc):
                return eval('True'), []
        """)
        assert loaded == []

    def test_getattr_rejected(self, tmp_path: Path) -> None:
        _, loaded = self._write_and_load(tmp_path, """\
            def validate_strategy(s, sc):
                return getattr(s, 'x', True), []
        """)
        assert loaded == []

    def test_globals_dunder_rejected(self, tmp_path: Path) -> None:
        _, loaded = self._write_and_load(tmp_path, """\
            def validate_strategy(s, sc):
                g = validate_strategy.__globals__
                return True, []
        """)
        assert loaded == []

    def test_type_not_in_builtins(self) -> None:
        assert "type" not in _SAFE_BUILTINS

    def test_open_rejected(self, tmp_path: Path) -> None:
        _, loaded = self._write_and_load(tmp_path, """\
            def validate_strategy(s, sc):
                f = open('/etc/passwd')
                return True, []
        """)
        assert loaded == []

    def test_infinite_loop_timeout_on_load(self, tmp_path: Path) -> None:
        _, loaded = self._write_and_load(tmp_path, """\
            while True:
                pass
            def validate_strategy(s, sc):
                return True, []
        """, timeout=0.5)
        assert loaded == []

    def test_infinite_loop_timeout_on_validate(self, tmp_path: Path) -> None:
        loader, loaded = self._write_and_load(tmp_path, """\
            def validate_strategy(s, sc):
                while True:
                    pass
                return True, []
        """, timeout=0.5)
        assert loaded == ["test"]
        start = time.monotonic()
        result = loader.validate_strategy({}, None)
        elapsed = time.monotonic() - start
        assert not result.passed
        assert any("timed out" in e for e in result.errors)
        assert elapsed < 3.0  # should be ~0.5s, generous upper bound

    def test_safe_validators_still_work(self, tmp_path: Path) -> None:
        loader, loaded = self._write_and_load(tmp_path, """\
            def validate_strategy(strategy, scenario):
                if "moves" not in strategy:
                    return False, ["missing moves"]
                return True, []
        """)
        assert loaded == ["test"]
        result = loader.validate_strategy({"moves": [1]}, None)
        assert result.passed

    def test_mixed_safe_unsafe_loading(self, tmp_path: Path) -> None:
        h_dir = tmp_path / "harness"
        h_dir.mkdir(parents=True, exist_ok=True)
        (h_dir / "bad.py").write_text("import os\ndef validate_strategy(s, sc): return True, []\n", encoding="utf-8")
        (h_dir / "good.py").write_text(
            "def validate_strategy(s, sc): return True, []\n", encoding="utf-8",
        )
        loader = HarnessLoader(h_dir)
        loaded = loader.load()
        assert "good" in loaded
        assert "bad" not in loaded
