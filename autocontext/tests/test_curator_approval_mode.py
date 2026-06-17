from autocontext.config import load_settings
from autocontext.server.protocol import StartRunCmd


def test_settings_default_curator_approval_mode() -> None:
    assert load_settings().curator_approval_mode == "auto"


def test_start_run_cmd_accepts_mode() -> None:
    cmd = StartRunCmd(scenario="grid_ctf", generations=3, curator_approval_mode="approve")
    assert cmd.curator_approval_mode == "approve"


def test_start_run_cmd_defaults_to_auto() -> None:
    cmd = StartRunCmd(scenario="grid_ctf", generations=3)
    assert cmd.curator_approval_mode == "auto"
