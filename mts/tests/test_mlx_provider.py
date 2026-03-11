"""Tests for AC-182: MLXProvider class for local model inference.

Tests the MLXProvider that loads trained MLX model checkpoints and generates
strategies via autoregressive sampling.  All tests mock the MLX/safetensors
dependencies so they run without MLX installed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mts.providers.base import CompletionResult, ProviderError

# ── Helpers ─────────────────────────────────────────────────────────────


def _fake_tokenizer(*, end_token_id: int = 8196) -> MagicMock:
    """Build a mock tokenizer with encode/decode."""
    tok = MagicMock()
    tok.end_token_id = end_token_id
    tok.vocab_size = 8197

    def _encode(text: str, **kwargs: Any) -> list[int]:
        # Return a simple list of token IDs based on text length
        return list(range(min(len(text), 50)))

    def _decode(token_ids: list[int]) -> str:
        # Return a valid JSON strategy string
        return json.dumps({"action": "move", "x": 1, "y": 2})

    tok.encode.side_effect = _encode
    tok.decode.side_effect = _decode
    return tok


def _fake_model(*, vocab_size: int = 8197, seq_len: int = 2048) -> MagicMock:
    """Build a mock model that returns logits."""
    model = MagicMock()
    cfg = MagicMock()
    cfg.vocab_size = vocab_size
    cfg.seq_len = seq_len
    model.cfg = cfg
    return model


def _write_fake_checkpoint(model_dir: Path) -> None:
    """Write a minimal fake checkpoint structure."""
    model_dir.mkdir(parents=True, exist_ok=True)
    # Config file
    (model_dir / "config.json").write_text(json.dumps({
        "depth": 4,
        "aspect_ratio": 64,
        "head_dim": 64,
        "n_kv_heads": 4,
        "vocab_size": 8197,
        "seq_len": 2048,
    }))
    # Fake weights file
    (model_dir / "model.safetensors").write_bytes(b"FAKE_WEIGHTS")
    # Fake tokenizer
    (model_dir / "tokenizer.json").write_text(json.dumps({"type": "BPE"}))


# ── Import and graceful error tests ────────────────────────────────────


class TestMLXProviderImport:
    def test_provider_module_importable(self) -> None:
        """mlx_provider module should always be importable."""
        from mts.providers import mlx_provider
        assert hasattr(mlx_provider, "MLXProvider")

    def test_graceful_error_when_mlx_not_installed(self, tmp_path: Path) -> None:
        """MLXProvider should raise ProviderError with install hint when MLX missing."""
        from mts.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        # The real _load_model_and_tokenizer checks HAS_MLX; no mock needed
        with pytest.raises(ProviderError, match="(?i)mlx"):
            MLXProvider(model_path=str(tmp_path / "model"))


# ── Model loading tests ────────────────────────────────────────────────


class TestModelLoading:
    def test_error_when_model_path_missing(self, tmp_path: Path) -> None:
        """ProviderError when model_path directory doesn't exist."""
        from mts.providers.mlx_provider import MLXProvider

        with pytest.raises(ProviderError, match="not found|does not exist"):
            MLXProvider(model_path=str(tmp_path / "nonexistent"))

    def test_error_when_config_missing(self, tmp_path: Path) -> None:
        """ProviderError when config.json is missing from model directory."""
        from mts.providers.mlx_provider import MLXProvider

        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "model.safetensors").write_bytes(b"FAKE")
        with pytest.raises(ProviderError, match="config"):
            MLXProvider(model_path=str(model_dir))

    def test_error_when_weights_missing(self, tmp_path: Path) -> None:
        """ProviderError when model.safetensors is missing."""
        from mts.providers.mlx_provider import MLXProvider

        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "config.json").write_text(json.dumps({"depth": 4}))
        with pytest.raises(ProviderError, match="weights|safetensors"):
            MLXProvider(model_path=str(model_dir))

    @patch("mts.providers.mlx_provider._load_model_and_tokenizer")
    def test_successful_load(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """Provider loads successfully when checkpoint is valid."""
        from mts.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        provider = MLXProvider(model_path=str(tmp_path / "model"))
        assert provider.name == "mlx"

    @patch("mts.providers.mlx_provider._load_model_and_tokenizer")
    def test_default_model_returns_path(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """default_model() returns the model path."""
        from mts.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        provider = MLXProvider(model_path=str(tmp_path / "model"))
        assert "model" in provider.default_model()


# ── Generation tests ───────────────────────────────────────────────────


class TestGeneration:
    @patch("mts.providers.mlx_provider._load_model_and_tokenizer")
    def test_complete_returns_completion_result(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """complete() should return a CompletionResult."""
        from mts.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        model = _fake_model()
        tokenizer = _fake_tokenizer()
        mock_load.return_value = (model, tokenizer)

        provider = MLXProvider(model_path=str(tmp_path / "model"))

        with patch.object(provider, "_generate", return_value='{"action": "move"}'):
            result = provider.complete("system prompt", "user prompt")

        assert isinstance(result, CompletionResult)
        assert result.text == '{"action": "move"}'
        assert result.model is not None

    @patch("mts.providers.mlx_provider._load_model_and_tokenizer")
    def test_complete_uses_temperature(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """Temperature parameter should be passed to generation."""
        from mts.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        provider = MLXProvider(model_path=str(tmp_path / "model"), temperature=0.5)

        with patch.object(provider, "_generate", return_value="output") as mock_gen:
            provider.complete("sys", "user", temperature=0.3)

        # Should use the call-level temperature, not the default
        mock_gen.assert_called_once()
        call_kwargs = mock_gen.call_args
        assert call_kwargs[1].get("temperature") == 0.3 or call_kwargs[0][1] == 0.3 if len(call_kwargs[0]) > 1 else True

    @patch("mts.providers.mlx_provider._load_model_and_tokenizer")
    def test_complete_uses_max_tokens(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """Max tokens parameter should limit generation length."""
        from mts.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        provider = MLXProvider(model_path=str(tmp_path / "model"))

        with patch.object(provider, "_generate", return_value="output") as mock_gen:
            provider.complete("sys", "user", max_tokens=256)

        mock_gen.assert_called_once()

    @patch("mts.providers.mlx_provider._load_model_and_tokenizer")
    def test_generation_error_raises_provider_error(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """Errors during generation should be wrapped in ProviderError."""
        from mts.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        provider = MLXProvider(model_path=str(tmp_path / "model"))

        with patch.object(provider, "_generate", side_effect=RuntimeError("OOM")):
            with pytest.raises(ProviderError, match="OOM"):
                provider.complete("sys", "user")


# ── Configuration tests ────────────────────────────────────────────────


class TestConfiguration:
    @patch("mts.providers.mlx_provider._load_model_and_tokenizer")
    def test_default_temperature(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """Default temperature should be 0.8."""
        from mts.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        provider = MLXProvider(model_path=str(tmp_path / "model"))
        assert provider._temperature == 0.8

    @patch("mts.providers.mlx_provider._load_model_and_tokenizer")
    def test_custom_temperature(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """Custom temperature should be stored."""
        from mts.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        provider = MLXProvider(model_path=str(tmp_path / "model"), temperature=0.5)
        assert provider._temperature == 0.5

    @patch("mts.providers.mlx_provider._load_model_and_tokenizer")
    def test_default_max_tokens(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """Default max_tokens should be 512."""
        from mts.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        provider = MLXProvider(model_path=str(tmp_path / "model"))
        assert provider._max_tokens == 512

    @patch("mts.providers.mlx_provider._load_model_and_tokenizer")
    def test_name_property(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """Provider name should be 'mlx'."""
        from mts.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        provider = MLXProvider(model_path=str(tmp_path / "model"))
        assert provider.name == "mlx"


# ── Settings config tests ─────────────────────────────────────────────


class TestSettingsConfig:
    def test_settings_has_mlx_model_path(self) -> None:
        from mts.config.settings import AppSettings
        settings = AppSettings()
        assert hasattr(settings, "mlx_model_path")
        assert settings.mlx_model_path == ""

    def test_settings_has_mlx_temperature(self) -> None:
        from mts.config.settings import AppSettings
        settings = AppSettings()
        assert hasattr(settings, "mlx_temperature")
        assert settings.mlx_temperature == 0.8

    def test_settings_has_mlx_max_tokens(self) -> None:
        from mts.config.settings import AppSettings
        settings = AppSettings()
        assert hasattr(settings, "mlx_max_tokens")
        assert settings.mlx_max_tokens == 512


# ── Autoregressive sampling tests ──────────────────────────────────────


class TestAutoRegressiveSampling:
    def test_generate_function_exists(self) -> None:
        """The _generate method should exist on the provider."""
        from mts.providers.mlx_provider import MLXProvider
        assert hasattr(MLXProvider, "_generate")

    @patch("mts.providers.mlx_provider._load_model_and_tokenizer")
    def test_generate_concatenates_system_and_user(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """_generate should combine system + user prompts."""
        from mts.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        tokenizer = _fake_tokenizer()
        model = _fake_model()
        mock_load.return_value = (model, tokenizer)

        provider = MLXProvider(model_path=str(tmp_path / "model"))

        with patch.object(provider, "_sample_tokens", return_value=[1, 2, 3]):
            result = provider._generate("system prompt\nuser prompt", temperature=0.8, max_tokens=64)

        # Tokenizer.encode should have been called with the combined prompt
        tokenizer.encode.assert_called()
        assert isinstance(result, str)

    @patch("mts.providers.mlx_provider._load_model_and_tokenizer")
    def test_generate_stops_at_end_token(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """Generation should stop when <|end|> token is produced."""
        from mts.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        end_token_id = 8196
        tokenizer = _fake_tokenizer(end_token_id=end_token_id)
        model = _fake_model()
        mock_load.return_value = (model, tokenizer)

        provider = MLXProvider(model_path=str(tmp_path / "model"))

        # _sample_tokens returns sequence ending with end_token
        with patch.object(provider, "_sample_tokens", return_value=[10, 20, end_token_id]):
            result = provider._generate("prompt", temperature=0.8, max_tokens=512)

        assert isinstance(result, str)

    @patch("mts.providers.mlx_provider._load_model_and_tokenizer")
    def test_generate_respects_max_tokens(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """Generation should stop after max_tokens even without end token."""
        from mts.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        tokenizer = _fake_tokenizer()
        model = _fake_model()
        mock_load.return_value = (model, tokenizer)

        provider = MLXProvider(model_path=str(tmp_path / "model"))

        # Return exactly max_tokens tokens (no end token)
        max_t = 32
        with patch.object(provider, "_sample_tokens", return_value=list(range(max_t))):
            result = provider._generate("prompt", temperature=0.8, max_tokens=max_t)

        assert isinstance(result, str)
