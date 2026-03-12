"""Tests for rlm_backend setting and ReplWorkerProtocol."""
from __future__ import annotations

import os
from unittest.mock import patch

from autocontext.config.settings import AppSettings, load_settings


class TestRlmBackendSetting:
    def test_default_rlm_backend_is_exec(self) -> None:
        settings = AppSettings()
        assert settings.rlm_backend == "exec"

    def test_rlm_backend_monty_accepted(self) -> None:
        settings = AppSettings(rlm_backend="monty")
        assert settings.rlm_backend == "monty"

    def test_load_settings_reads_rlm_backend_env(self) -> None:
        with patch.dict(os.environ, {"AUTOCONTEXT_RLM_BACKEND": "monty"}, clear=False):
            settings = load_settings()
        assert settings.rlm_backend == "monty"

    def test_load_settings_defaults_to_exec(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "AUTOCONTEXT_RLM_BACKEND"}
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()
        assert settings.rlm_backend == "exec"


class TestReplWorkerProtocol:
    def test_repl_worker_satisfies_protocol(self) -> None:
        from autocontext.harness.repl.types import ReplWorkerProtocol
        from autocontext.harness.repl.worker import ReplWorker

        worker = ReplWorker()
        assert isinstance(worker, ReplWorkerProtocol)

    def test_protocol_has_run_code_and_namespace(self) -> None:
        from autocontext.harness.repl.types import ReplWorkerProtocol

        assert hasattr(ReplWorkerProtocol, "run_code")
        assert hasattr(ReplWorkerProtocol, "namespace")
