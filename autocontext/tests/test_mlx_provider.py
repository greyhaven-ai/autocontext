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

from autocontext.providers.base import CompletionResult, ProviderError

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


def _fake_serializable_tokenizer() -> MagicMock:
    """Build a tokenizer with the metadata needed for JSON serialization."""
    tok = _fake_tokenizer()
    encoding = MagicMock()
    encoding._mergeable_ranks = {b"a": 0, b"b": 1}
    encoding._pat_str = r"\w+|\s+"
    tok._encoding = encoding
    tok.base_vocab_size = 256
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
        from autocontext.providers import mlx_provider
        assert hasattr(mlx_provider, "MLXProvider")

    def test_graceful_error_when_mlx_not_installed(self, tmp_path: Path) -> None:
        """MLXProvider should raise ProviderError with install hint when MLX missing."""
        from autocontext.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        # The real _load_model_and_tokenizer checks HAS_MLX; no mock needed
        with pytest.raises(ProviderError, match="(?i)mlx"):
            MLXProvider(model_path=str(tmp_path / "model"))


# ── Model loading tests ────────────────────────────────────────────────


class TestModelLoading:
    def test_error_when_model_path_missing(self, tmp_path: Path) -> None:
        """ProviderError when model_path directory doesn't exist."""
        from autocontext.providers.mlx_provider import MLXProvider

        with pytest.raises(ProviderError, match="not found|does not exist"):
            MLXProvider(model_path=str(tmp_path / "nonexistent"))

    def test_error_when_config_missing(self, tmp_path: Path) -> None:
        """ProviderError when config.json is missing from model directory."""
        from autocontext.providers.mlx_provider import MLXProvider

        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "model.safetensors").write_bytes(b"FAKE")
        with pytest.raises(ProviderError, match="config"):
            MLXProvider(model_path=str(model_dir))

    def test_error_when_weights_missing(self, tmp_path: Path) -> None:
        """ProviderError when model.safetensors is missing."""
        from autocontext.providers.mlx_provider import MLXProvider

        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "config.json").write_text(json.dumps({"depth": 4}))
        with pytest.raises(ProviderError, match="weights|safetensors"):
            MLXProvider(model_path=str(model_dir))

    @patch("autocontext.providers.mlx_provider._load_model_and_tokenizer")
    def test_successful_load(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """Provider loads successfully when checkpoint is valid."""
        from autocontext.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        provider = MLXProvider(model_path=str(tmp_path / "model"))
        assert provider.name == "mlx"

    @patch("autocontext.providers.mlx_provider._load_model_and_tokenizer")
    def test_default_model_returns_path(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """default_model() returns the model path."""
        from autocontext.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        provider = MLXProvider(model_path=str(tmp_path / "model"))
        assert "model" in provider.default_model()


# ── Generation tests ───────────────────────────────────────────────────


class TestGeneration:
    @patch("autocontext.providers.mlx_provider._load_model_and_tokenizer")
    def test_complete_returns_completion_result(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """complete() should return a CompletionResult."""
        from autocontext.providers.mlx_provider import MLXProvider

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

    @patch("autocontext.providers.mlx_provider._load_model_and_tokenizer")
    def test_complete_uses_temperature(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """Temperature parameter should be passed to generation."""
        from autocontext.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        provider = MLXProvider(model_path=str(tmp_path / "model"), temperature=0.5)

        with patch.object(provider, "_generate", return_value="output") as mock_gen:
            provider.complete("sys", "user", temperature=0.3)

        # Should use the call-level temperature, not the default
        mock_gen.assert_called_once()
        _, kwargs = mock_gen.call_args
        assert kwargs["temperature"] == 0.3

    @patch("autocontext.providers.mlx_provider._load_model_and_tokenizer")
    def test_complete_uses_max_tokens(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """Max tokens parameter should limit generation length."""
        from autocontext.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        provider = MLXProvider(model_path=str(tmp_path / "model"))

        with patch.object(provider, "_generate", return_value="output") as mock_gen:
            provider.complete("sys", "user", max_tokens=256)

        mock_gen.assert_called_once()

    @patch("autocontext.providers.mlx_provider._load_model_and_tokenizer")
    def test_generation_error_raises_provider_error(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """Errors during generation should be wrapped in ProviderError."""
        from autocontext.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        provider = MLXProvider(model_path=str(tmp_path / "model"))

        with patch.object(provider, "_generate", side_effect=RuntimeError("OOM")):
            with pytest.raises(ProviderError, match="OOM"):
                provider.complete("sys", "user")


# ── Configuration tests ────────────────────────────────────────────────


class TestConfiguration:
    @patch("autocontext.providers.mlx_provider._load_model_and_tokenizer")
    def test_default_temperature(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """Default temperature should be 0.8."""
        from autocontext.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        provider = MLXProvider(model_path=str(tmp_path / "model"))
        assert provider._temperature == 0.8

    @patch("autocontext.providers.mlx_provider._load_model_and_tokenizer")
    def test_custom_temperature(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """Custom temperature should be stored."""
        from autocontext.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        provider = MLXProvider(model_path=str(tmp_path / "model"), temperature=0.5)
        assert provider._temperature == 0.5

    @patch("autocontext.providers.mlx_provider._load_model_and_tokenizer")
    def test_default_max_tokens(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """Default max_tokens should be 512."""
        from autocontext.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        provider = MLXProvider(model_path=str(tmp_path / "model"))
        assert provider._max_tokens == 512

    @patch("autocontext.providers.mlx_provider._load_model_and_tokenizer")
    def test_name_property(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """Provider name should be 'mlx'."""
        from autocontext.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        provider = MLXProvider(model_path=str(tmp_path / "model"))
        assert provider.name == "mlx"


# ── Settings config tests ─────────────────────────────────────────────


class TestSettingsConfig:
    def test_settings_has_mlx_model_path(self) -> None:
        from autocontext.config.settings import AppSettings
        settings = AppSettings()
        assert hasattr(settings, "mlx_model_path")
        assert settings.mlx_model_path == ""

    def test_settings_has_mlx_temperature(self) -> None:
        from autocontext.config.settings import AppSettings
        settings = AppSettings()
        assert hasattr(settings, "mlx_temperature")
        assert settings.mlx_temperature == 0.8

    def test_settings_has_mlx_max_tokens(self) -> None:
        from autocontext.config.settings import AppSettings
        settings = AppSettings()
        assert hasattr(settings, "mlx_max_tokens")
        assert settings.mlx_max_tokens == 512


# ── Autoregressive sampling tests ──────────────────────────────────────


class TestAutoRegressiveSampling:
    def test_generate_function_exists(self) -> None:
        """The _generate method should exist on the provider."""
        from autocontext.providers.mlx_provider import MLXProvider
        assert hasattr(MLXProvider, "_generate")

    @patch("autocontext.providers.mlx_provider._load_model_and_tokenizer")
    def test_generate_concatenates_system_and_user(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """_generate should combine system + user prompts."""
        from autocontext.providers.mlx_provider import MLXProvider

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

    @patch("autocontext.providers.mlx_provider._load_model_and_tokenizer")
    def test_generate_stops_at_end_token(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """Generation should stop when <|end|> token is produced."""
        from autocontext.providers.mlx_provider import MLXProvider

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

    @patch("autocontext.providers.mlx_provider._load_model_and_tokenizer")
    def test_generate_decodes_only_generated_tokens(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """The provider should not echo prompt tokens back in the returned text."""
        from autocontext.providers.mlx_provider import MLXProvider

        _write_fake_checkpoint(tmp_path / "model")
        tokenizer = _fake_tokenizer()
        tokenizer.encode.side_effect = lambda text, **kwargs: [1, 2, 3]
        tokenizer.decode.side_effect = lambda token_ids: '{"action": "move"}'
        mock_load.return_value = (_fake_model(), tokenizer)

        provider = MLXProvider(model_path=str(tmp_path / "model"))
        with patch.object(provider, "_sample_tokens", return_value=[1, 2, 3, 10, 20, tokenizer.end_token_id]):
            result = provider._generate("prompt", temperature=0.8, max_tokens=32)

        tokenizer.decode.assert_called_once_with([10, 20])
        assert result == '{"action": "move"}'

    @patch("autocontext.providers.mlx_provider._load_model_and_tokenizer")
    def test_generate_respects_max_tokens(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """Generation should stop after max_tokens even without end token."""
        from autocontext.providers.mlx_provider import MLXProvider

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


# ── Registry wiring tests ──────────────────────────────────────────────


class TestRegistryWiring:
    """Verify MLXProvider is reachable through the provider factory."""

    @patch("autocontext.providers.mlx_provider._load_model_and_tokenizer")
    def test_create_provider_mlx(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """create_provider('mlx', model=<path>) returns an MLXProvider."""
        from autocontext.providers.registry import create_provider

        _write_fake_checkpoint(tmp_path / "model")
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        provider = create_provider("mlx", model=str(tmp_path / "model"))
        assert provider.name == "mlx"
        assert provider.default_model() == str(tmp_path / "model")

    @patch("autocontext.providers.mlx_provider._load_model_and_tokenizer")
    def test_create_provider_mlx_case_insensitive(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """create_provider('MLX') should also work (case-insensitive)."""
        from autocontext.providers.registry import create_provider

        _write_fake_checkpoint(tmp_path / "model")
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        provider = create_provider("MLX", model=str(tmp_path / "model"))
        assert provider.name == "mlx"

    def test_create_provider_mlx_no_path_raises(self) -> None:
        """create_provider('mlx') without model path should raise ProviderError."""
        from autocontext.providers.registry import create_provider

        with pytest.raises(ProviderError, match="model_path|model path|does not exist"):
            create_provider("mlx")

    @patch("autocontext.providers.mlx_provider._load_model_and_tokenizer")
    def test_get_provider_mlx_from_settings(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """get_provider() with judge_provider='mlx' creates an MLXProvider."""
        from autocontext.config.settings import AppSettings
        from autocontext.providers.registry import get_provider

        _write_fake_checkpoint(tmp_path / "model")
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        settings = AppSettings(
            judge_provider="mlx",
            mlx_model_path=str(tmp_path / "model"),
            mlx_temperature=0.5,
            mlx_max_tokens=256,
        )
        provider = get_provider(settings)
        assert provider.name == "mlx"
        assert provider._temperature == 0.5
        assert provider._max_tokens == 256

    @patch("autocontext.providers.mlx_provider._load_model_and_tokenizer")
    def test_get_provider_mlx_uses_settings_defaults(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """get_provider() should forward mlx_temperature and mlx_max_tokens from settings."""
        from autocontext.config.settings import AppSettings
        from autocontext.providers.registry import get_provider

        _write_fake_checkpoint(tmp_path / "model")
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        settings = AppSettings(
            judge_provider="mlx",
            mlx_model_path=str(tmp_path / "model"),
        )
        provider = get_provider(settings)
        assert provider._temperature == 0.8  # default
        assert provider._max_tokens == 512  # default

    def test_error_message_includes_mlx_in_supported_list(self) -> None:
        """Unknown provider error should list 'mlx' as a supported type."""
        from autocontext.providers.registry import create_provider

        with pytest.raises(ProviderError, match="mlx"):
            create_provider("magic-llm")


class TestAgentLoopWiring:
    @patch("autocontext.agents.llm_client.MLXProvider")
    def test_build_client_from_settings_supports_mlx(self, mock_provider: MagicMock, tmp_path: Path) -> None:
        """The main agent loop should be able to build an MLX-backed client."""
        from autocontext.agents.llm_client import MLXClient, build_client_from_settings
        from autocontext.config.settings import AppSettings

        mock_instance = MagicMock()
        mock_instance.default_model.return_value = str(tmp_path / "bundle")
        mock_instance.complete.return_value = CompletionResult(text='{"action": "move"}', model=str(tmp_path / "bundle"))
        mock_provider.return_value = mock_instance

        settings = AppSettings(agent_provider="mlx", mlx_model_path=str(tmp_path / "bundle"))
        client = build_client_from_settings(settings)
        assert isinstance(client, MLXClient)

    @patch("autocontext.agents.llm_client.MLXProvider")
    def test_mlx_client_generate_uses_provider_completion(self, mock_provider: MagicMock, tmp_path: Path) -> None:
        """MLXClient should adapt provider completions into ModelResponse for agents."""
        from autocontext.agents.llm_client import MLXClient

        mock_instance = MagicMock()
        mock_instance.default_model.return_value = str(tmp_path / "bundle")
        mock_instance.complete.return_value = CompletionResult(
            text='{"action": "move"}',
            model=str(tmp_path / "bundle"),
            usage={"input_tokens": 11, "output_tokens": 5},
        )
        mock_provider.return_value = mock_instance

        client = MLXClient(str(tmp_path / "bundle"))
        response = client.generate(
            model="ignored",
            prompt="describe your strategy",
            max_tokens=128,
            temperature=0.3,
        )
        assert response.text == '{"action": "move"}'
        assert response.usage.input_tokens == 11
        assert response.usage.output_tokens == 5


class TestBundleCompatibility:
    def test_save_tokenizer_json_persists_provider_format(self, tmp_path: Path) -> None:
        from autocontext.training.autoresearch.prepare import save_tokenizer_json

        tokenizer = _fake_serializable_tokenizer()
        path = tmp_path / "tokenizer.json"
        save_tokenizer_json(tokenizer, path)

        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["base_vocab_size"] == 256
        assert "mergeable_ranks" in payload
        assert "pat_str" in payload

    @patch("autocontext.training.autoresearch.train.save_checkpoint")
    def test_save_inference_bundle_writes_provider_artifacts(self, mock_save_checkpoint: MagicMock, tmp_path: Path) -> None:
        from autocontext.training.autoresearch.train import ModelConfig, save_inference_bundle

        bundle_dir = tmp_path / "bundle"
        tokenizer = _fake_serializable_tokenizer()
        model = MagicMock()

        save_inference_bundle(model, ModelConfig(), tokenizer, bundle_dir)

        assert (bundle_dir / "config.json").exists()
        assert (bundle_dir / "tokenizer.json").exists()
        mock_save_checkpoint.assert_called_once_with(model, bundle_dir / "model.safetensors")
