from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from autocontext.config.settings import AppSettings
from autocontext.training.runner import TrainingConfig, TrainingRunner


@patch("autocontext.training.runner.build_client_from_settings")
@patch("autocontext.training.runner.load_settings")
def test_training_agent_client_passes_scenario_name(
    mock_load_settings,
    mock_build_client,
    tmp_path: Path,
) -> None:
    settings = AppSettings(agent_provider="anthropic", anthropic_api_key="test-key")
    mock_load_settings.return_value = settings
    mock_build_client.return_value = object()

    config = TrainingConfig(
        scenario="grid_ctf",
        data_path=tmp_path / "train.jsonl",
        agent_provider="pi",
    )
    runner = TrainingRunner(config, work_dir=tmp_path / "workspace")

    result = runner._build_agent_client()

    assert result is mock_build_client.return_value
    resolved_settings = settings.model_copy(update={"agent_provider": "pi"})
    mock_build_client.assert_called_once_with(resolved_settings, scenario_name="grid_ctf")
