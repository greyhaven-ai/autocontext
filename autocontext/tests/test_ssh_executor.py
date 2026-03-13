"""Tests for AC-213: Trusted SSH executor for user-owned research machines.

TDD test suite covering:
- SSHHostConfig / SSHHostCapabilities data models
- SSHCommandResult value type
- SSHClient command execution, file transfer, health checks
- SSHExecutor implementing ExecutionEngine protocol
- AppSettings SSH fields
- Generation runner wiring for executor_mode="ssh"
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from autocontext.config.settings import AppSettings
from autocontext.execution.executors.ssh import SSHExecutor
from autocontext.integrations.ssh.client import SSHClient, SSHCommandResult
from autocontext.integrations.ssh.config import SSHHostCapabilities, SSHHostConfig
from autocontext.scenarios.base import ExecutionLimits, ReplayEnvelope, Result

# ===========================================================================
# SSHHostCapabilities
# ===========================================================================


class TestSSHHostCapabilities:
    def test_defaults(self) -> None:
        cap = SSHHostCapabilities()
        assert cap.cpu_cores == 0
        assert cap.memory_gb == 0.0
        assert cap.gpu_count == 0
        assert cap.gpu_model == ""
        assert cap.installed_runtimes == []

    def test_custom_values(self) -> None:
        cap = SSHHostCapabilities(
            cpu_cores=16,
            memory_gb=64.0,
            gpu_count=2,
            gpu_model="A100",
            installed_runtimes=["python3.11", "node18"],
        )
        assert cap.cpu_cores == 16
        assert cap.gpu_model == "A100"
        assert len(cap.installed_runtimes) == 2


# ===========================================================================
# SSHHostConfig
# ===========================================================================


class TestSSHHostConfig:
    def test_minimal_config(self) -> None:
        cfg = SSHHostConfig(name="lab-box", hostname="192.168.1.100")
        assert cfg.name == "lab-box"
        assert cfg.hostname == "192.168.1.100"
        assert cfg.port == 22
        assert cfg.user == ""
        assert cfg.identity_file == ""
        assert cfg.working_directory == "/tmp/autocontext"
        assert cfg.environment == {}
        assert cfg.connect_timeout == 10
        assert cfg.command_timeout == 120.0

    def test_full_config(self) -> None:
        cfg = SSHHostConfig(
            name="gpu-server",
            hostname="gpu.lab.internal",
            port=2222,
            user="researcher",
            identity_file="~/.ssh/lab_key",
            working_directory="/home/researcher/autocontext",
            environment={"CUDA_VISIBLE_DEVICES": "0,1"},
            capabilities=SSHHostCapabilities(cpu_cores=32, memory_gb=128.0, gpu_count=4),
            connect_timeout=30,
            command_timeout=600.0,
        )
        assert cfg.port == 2222
        assert cfg.user == "researcher"
        assert cfg.capabilities.gpu_count == 4
        assert cfg.environment["CUDA_VISIBLE_DEVICES"] == "0,1"

    def test_hostname_required(self) -> None:
        with pytest.raises((TypeError, ValueError)):
            SSHHostConfig(name="missing-host")  # type: ignore[call-arg]

    def test_name_required(self) -> None:
        with pytest.raises((TypeError, ValueError)):
            SSHHostConfig(hostname="host")  # type: ignore[call-arg]


# ===========================================================================
# SSHCommandResult
# ===========================================================================


class TestSSHCommandResult:
    def test_construction(self) -> None:
        r = SSHCommandResult(
            exit_code=0,
            stdout="hello\n",
            stderr="",
            duration_ms=150,
        )
        assert r.exit_code == 0
        assert r.stdout == "hello\n"
        assert r.success is True

    def test_failure(self) -> None:
        r = SSHCommandResult(exit_code=1, stdout="", stderr="error", duration_ms=50)
        assert r.success is False


# ===========================================================================
# SSHClient — command execution
# ===========================================================================


class TestSSHClientExecute:
    def _make_client(self, **overrides: Any) -> SSHClient:
        cfg = SSHHostConfig(name="test", hostname="testhost", **overrides)
        return SSHClient(cfg)

    def test_execute_command_success(self) -> None:
        client = self._make_client()
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="output\n", stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = client.execute_command("echo hello")
        assert result.exit_code == 0
        assert result.stdout == "output\n"

    def test_execute_command_builds_ssh_args(self) -> None:
        client = self._make_client(user="admin", port=2222, identity_file="/key")
        captured_args: list[str] = []
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        def capture_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured_args.extend(args)
            return mock_result

        with patch("subprocess.run", side_effect=capture_run):
            client.execute_command("ls")
        assert "ssh" in captured_args[0]
        assert "-p" in captured_args
        assert "2222" in captured_args
        assert "-i" in captured_args
        assert "/key" in captured_args
        assert "admin@testhost" in captured_args

    def test_execute_command_timeout(self) -> None:
        client = self._make_client(command_timeout=5.0)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=5.0)):
            result = client.execute_command("long-running")
        assert result.exit_code == -1
        assert "timed out" in result.stderr.lower()

    def test_execute_command_with_environment(self) -> None:
        client = self._make_client(environment={"FOO": "bar", "BAZ": "qux"})
        captured_args: list[str] = []
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        def capture_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured_args.extend(args)
            return mock_result

        with patch("subprocess.run", side_effect=capture_run):
            client.execute_command("echo test")
        cmd_str = " ".join(captured_args)
        assert "FOO=bar" in cmd_str or "FOO='bar'" in cmd_str

    def test_execute_nonzero_exit(self) -> None:
        client = self._make_client()
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=127, stdout="", stderr="command not found",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = client.execute_command("nonexistent")
        assert result.exit_code == 127
        assert result.success is False


# ===========================================================================
# SSHClient — health check
# ===========================================================================


class TestSSHClientHealthCheck:
    def test_health_check_success(self) -> None:
        cfg = SSHHostConfig(name="test", hostname="testhost")
        client = SSHClient(cfg)
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="testhost\n", stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            status = client.health_check()
        assert status["status"] == "healthy"
        assert status["host"] == "testhost"

    def test_health_check_unreachable(self) -> None:
        cfg = SSHHostConfig(name="test", hostname="badhost")
        client = SSHClient(cfg)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=10)):
            status = client.health_check()
        assert status["status"] == "unreachable"

    def test_health_check_connection_error(self) -> None:
        cfg = SSHHostConfig(name="test", hostname="badhost")
        client = SSHClient(cfg)
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=255, stdout="", stderr="Connection refused",
        )
        with patch("subprocess.run", return_value=mock_result):
            status = client.health_check()
        assert status["status"] == "error"


# ===========================================================================
# SSHClient — file transfer
# ===========================================================================


class TestSSHClientFileTransfer:
    def test_upload_file(self, tmp_path: Path) -> None:
        cfg = SSHHostConfig(name="test", hostname="testhost")
        client = SSHClient(cfg)
        local_file = tmp_path / "data.json"
        local_file.write_text('{"key": "value"}')
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            client.upload_file(local_file, "/remote/data.json")
        call_args = mock_run.call_args[0][0]
        assert "scp" in call_args[0]
        assert str(local_file) in call_args
        assert "testhost:/remote/data.json" in call_args

    def test_download_file(self, tmp_path: Path) -> None:
        cfg = SSHHostConfig(name="test", hostname="testhost")
        client = SSHClient(cfg)
        local_dest = tmp_path / "downloaded.json"
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            client.download_file("/remote/output.json", local_dest)
        call_args = mock_run.call_args[0][0]
        assert "scp" in call_args[0]
        assert "testhost:/remote/output.json" in call_args
        assert str(local_dest) in call_args

    def test_upload_file_with_user_and_port(self, tmp_path: Path) -> None:
        cfg = SSHHostConfig(name="test", hostname="testhost", user="admin", port=2222)
        client = SSHClient(cfg)
        local_file = tmp_path / "data.txt"
        local_file.write_text("data")
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            client.upload_file(local_file, "/remote/data.txt")
        call_args = mock_run.call_args[0][0]
        assert "-P" in call_args
        assert "2222" in call_args
        assert "admin@testhost:/remote/data.txt" in call_args

    def test_upload_failure_raises(self, tmp_path: Path) -> None:
        cfg = SSHHostConfig(name="test", hostname="testhost")
        client = SSHClient(cfg)
        local_file = tmp_path / "data.txt"
        local_file.write_text("data")
        mock_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="Permission denied")

        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="upload failed"):
                client.upload_file(local_file, "/remote/data.txt")


# ===========================================================================
# SSHClient — ensure working directory
# ===========================================================================


class TestSSHClientWorkingDir:
    def test_ensure_working_directory(self) -> None:
        cfg = SSHHostConfig(name="test", hostname="testhost", working_directory="/home/user/ac")
        client = SSHClient(cfg)
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        captured_args: list[str] = []

        def capture(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured_args.extend(args)
            return mock_result

        with patch("subprocess.run", side_effect=capture):
            client.ensure_working_directory()
        cmd_str = " ".join(captured_args)
        assert "mkdir" in cmd_str
        assert "/home/user/ac" in cmd_str


# ===========================================================================
# SSHExecutor — ExecutionEngine protocol
# ===========================================================================


class TestSSHExecutor:
    def _make_executor(self, **client_overrides: Any) -> tuple[SSHExecutor, SSHClient]:
        cfg = SSHHostConfig(name="test", hostname="testhost", **client_overrides)
        client = SSHClient(cfg)
        executor = SSHExecutor(client=client)
        return executor, client

    def test_execute_success(self) -> None:
        executor, client = self._make_executor()
        scenario = MagicMock()
        scenario.name = "grid_ctf"

        result_data = {
            "result": {
                "score": 0.75,
                "winner": "challenger",
                "summary": "test match",
                "replay": [],
                "metrics": {},
                "validation_errors": [],
            },
            "replay": {
                "scenario": "grid_ctf",
                "seed": 42,
                "narrative": "test",
                "timeline": [],
            },
        }
        # Mock the SSH command execution to return the strategy result
        mock_cmd_result = SSHCommandResult(
            exit_code=0,
            stdout=json.dumps(result_data),
            stderr="",
            duration_ms=500,
        )
        with patch.object(client, "execute_command", return_value=mock_cmd_result):
            with patch.object(client, "ensure_working_directory"):
                result, replay = executor.execute(
                    scenario=scenario,
                    strategy={"aggression": 0.6, "defense": 0.5, "path_bias": 0.55},
                    seed=42,
                    limits=ExecutionLimits(timeout_seconds=30.0),
                )
        assert isinstance(result, Result)
        assert result.score == 0.75
        assert isinstance(replay, ReplayEnvelope)
        assert replay.scenario == "grid_ctf"

    def test_execute_nonzero_exit_with_fallback(self) -> None:
        executor, client = self._make_executor()
        executor.allow_fallback = True
        scenario = MagicMock()
        scenario.name = "grid_ctf"

        mock_cmd_result = SSHCommandResult(
            exit_code=1, stdout="", stderr="error", duration_ms=100,
        )
        with patch.object(client, "execute_command", return_value=mock_cmd_result):
            with patch.object(client, "ensure_working_directory"):
                result, replay = executor.execute(
                    scenario=scenario,
                    strategy={"aggression": 0.5},
                    seed=1,
                    limits=ExecutionLimits(),
                )
        assert result.score == 0.0
        assert "unavailable" in result.summary.lower() or "failed" in result.summary.lower()

    def test_execute_nonzero_exit_without_fallback(self) -> None:
        executor, client = self._make_executor()
        executor.allow_fallback = False
        scenario = MagicMock()
        scenario.name = "grid_ctf"

        mock_cmd_result = SSHCommandResult(
            exit_code=1, stdout="", stderr="error", duration_ms=100,
        )
        with patch.object(client, "execute_command", return_value=mock_cmd_result):
            with patch.object(client, "ensure_working_directory"):
                with pytest.raises(RuntimeError):
                    executor.execute(
                        scenario=scenario,
                        strategy={"aggression": 0.5},
                        seed=1,
                        limits=ExecutionLimits(),
                    )

    def test_execute_invalid_json_with_fallback(self) -> None:
        executor, client = self._make_executor()
        scenario = MagicMock()
        scenario.name = "grid_ctf"

        mock_cmd_result = SSHCommandResult(
            exit_code=0, stdout="not json", stderr="", duration_ms=100,
        )
        with patch.object(client, "execute_command", return_value=mock_cmd_result):
            with patch.object(client, "ensure_working_directory"):
                result, replay = executor.execute(
                    scenario=scenario,
                    strategy={},
                    seed=1,
                    limits=ExecutionLimits(),
                )
        assert result.score == 0.0

    def test_execute_builds_eval_command(self) -> None:
        """Verify the executor sends a proper evaluation command."""
        executor, client = self._make_executor(working_directory="/work")
        scenario = MagicMock()
        scenario.name = "grid_ctf"

        captured_cmd: list[str] = []
        result_data = {
            "result": {"score": 0.5, "winner": None, "summary": "t", "replay": [], "metrics": {}, "validation_errors": []},
            "replay": {"scenario": "grid_ctf", "seed": 1, "narrative": "t", "timeline": []},
        }
        mock_cmd_result = SSHCommandResult(exit_code=0, stdout=json.dumps(result_data), stderr="", duration_ms=100)

        def capture_exec(cmd: str, **kwargs: Any) -> SSHCommandResult:
            captured_cmd.append(cmd)
            return mock_cmd_result

        with patch.object(client, "execute_command", side_effect=capture_exec):
            with patch.object(client, "ensure_working_directory"):
                executor.execute(
                    scenario=scenario,
                    strategy={"aggression": 0.6},
                    seed=42,
                    limits=ExecutionLimits(timeout_seconds=15.0),
                )
        assert len(captured_cmd) == 1
        assert "grid_ctf" in captured_cmd[0] or "autocontext" in captured_cmd[0].lower()

    def test_execute_timeout_in_limits(self) -> None:
        """Timeout from limits is passed to execute_command."""
        executor, client = self._make_executor()
        scenario = MagicMock()
        scenario.name = "grid_ctf"

        result_data = {
            "result": {"score": 0.5, "winner": None, "summary": "t", "replay": [], "metrics": {}, "validation_errors": []},
            "replay": {"scenario": "grid_ctf", "seed": 1, "narrative": "t", "timeline": []},
        }
        mock_cmd_result = SSHCommandResult(exit_code=0, stdout=json.dumps(result_data), stderr="", duration_ms=100)

        with patch.object(client, "execute_command", return_value=mock_cmd_result) as mock_exec:
            with patch.object(client, "ensure_working_directory"):
                executor.execute(
                    scenario=scenario,
                    strategy={},
                    seed=1,
                    limits=ExecutionLimits(timeout_seconds=42.0),
                )
        call_kwargs = mock_exec.call_args
        assert call_kwargs[1].get("timeout") == 42.0 or call_kwargs.kwargs.get("timeout") == 42.0


# ===========================================================================
# AppSettings — SSH fields
# ===========================================================================


class TestSSHSettings:
    def test_defaults(self) -> None:
        s = AppSettings()
        assert s.ssh_host == ""
        assert s.ssh_port == 22
        assert s.ssh_user == ""
        assert s.ssh_identity_file == ""
        assert s.ssh_working_directory == "/tmp/autocontext"
        assert s.ssh_connect_timeout == 10
        assert s.ssh_command_timeout == 120.0
        assert s.ssh_allow_fallback is True

    def test_custom_values(self) -> None:
        s = AppSettings(
            ssh_host="gpu.lab",
            ssh_port=2222,
            ssh_user="researcher",
            ssh_identity_file="/keys/lab",
            ssh_working_directory="/home/researcher/ac",
            ssh_connect_timeout=30,
            ssh_command_timeout=600.0,
            ssh_allow_fallback=False,
        )
        assert s.ssh_host == "gpu.lab"
        assert s.ssh_port == 2222
        assert s.ssh_allow_fallback is False
