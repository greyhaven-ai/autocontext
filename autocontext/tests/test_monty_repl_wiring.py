"""Tests for MontyReplWorker wiring: re-exports, prompts, and orchestrator backend selection."""
from __future__ import annotations


class TestMontyReplReExports:
    def test_monty_worker_re_exported_from_rlm(self) -> None:
        from autocontext.rlm.repl_worker import MontyReplWorker

        assert MontyReplWorker is not None

    def test_monty_worker_importable_from_harness(self) -> None:
        from autocontext.harness.repl.monty_worker import MontyReplWorker

        assert MontyReplWorker is not None


class TestMontyModePrompts:
    def test_monty_scaffolding_preamble_exists(self) -> None:
        from autocontext.rlm.prompts import MONTY_RLM_SCAFFOLDING_PREAMBLE

        assert len(MONTY_RLM_SCAFFOLDING_PREAMBLE) > 100

    def test_monty_preamble_explains_state_dict(self) -> None:
        from autocontext.rlm.prompts import MONTY_RLM_SCAFFOLDING_PREAMBLE

        assert "state[" in MONTY_RLM_SCAFFOLDING_PREAMBLE

    def test_monty_preamble_explains_stdlib(self) -> None:
        from autocontext.rlm.prompts import MONTY_RLM_SCAFFOLDING_PREAMBLE

        assert "stdlib(" in MONTY_RLM_SCAFFOLDING_PREAMBLE


class TestOrchestratorBackendSelection:
    def test_monty_backend_imports_monty_worker(self) -> None:
        """When rlm_backend='monty', orchestrator should be able to import MontyReplWorker."""
        from autocontext.harness.repl.monty_worker import MontyReplWorker
        from autocontext.rlm.prompts import ANALYST_MONTY_RLM_SYSTEM, ARCHITECT_MONTY_RLM_SYSTEM

        assert MontyReplWorker is not None
        assert "analyst" in ANALYST_MONTY_RLM_SYSTEM.lower() or "Analyst" in ANALYST_MONTY_RLM_SYSTEM
        assert "architect" in ARCHITECT_MONTY_RLM_SYSTEM.lower() or "Architect" in ARCHITECT_MONTY_RLM_SYSTEM
