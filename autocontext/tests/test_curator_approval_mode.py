import inspect

from autocontext.config import load_settings
from autocontext.loop.generation_runner import GenerationRunner
from autocontext.loop.stage_types import GenerationContext
from autocontext.server.protocol import StartRunCmd
from autocontext.server.run_manager import RunManager


def test_settings_default_curator_approval_mode() -> None:
    assert load_settings().curator_approval_mode == "auto"


def test_start_run_cmd_accepts_mode() -> None:
    cmd = StartRunCmd(scenario="grid_ctf", generations=3, curator_approval_mode="approve")
    assert cmd.curator_approval_mode == "approve"


def test_start_run_cmd_defaults_to_auto() -> None:
    cmd = StartRunCmd(scenario="grid_ctf", generations=3)
    assert cmd.curator_approval_mode == "auto"


def test_generation_context_has_mode_field() -> None:
    assert "curator_approval_mode" in GenerationContext.__dataclass_fields__


def test_runner_run_accepts_mode_param() -> None:
    assert "curator_approval_mode" in inspect.signature(GenerationRunner.run).parameters


def test_run_manager_start_run_accepts_mode_param() -> None:
    assert "curator_approval_mode" in inspect.signature(RunManager.start_run).parameters
