"""MLXProvider — local model inference via MLX autoregressive sampling.

Loads a trained MLX model checkpoint (safetensors) and generates strategies
using temperature-based sampling.  All MLX imports are behind guards so the
module is importable for type-checking even when MLX is not installed.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from autocontext.providers.base import CompletionResult, LLMProvider, ProviderError

LOGGER = logging.getLogger(__name__)


def _resolve_weights_path(model_dir: Path) -> Path:
    """Resolve the safetensors weights file from a model bundle directory."""
    preferred = model_dir / "model.safetensors"
    if preferred.exists():
        return preferred

    candidates = sorted(model_dir.glob("*.safetensors"))
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ProviderError(f"Model weights (safetensors) not found in: {model_dir}")
    raise ProviderError(f"Multiple safetensors checkpoints found in {model_dir}; expected model.safetensors")


def _load_model_and_tokenizer(model_dir: Path) -> tuple[Any, Any]:
    """Load a GPT model and tokenizer from a checkpoint directory.

    Requires MLX, safetensors, and the training module to be available.

    Args:
        model_dir: Directory containing config.json, model.safetensors, and tokenizer.json.

    Returns:
        (model, tokenizer) tuple.

    Raises:
        ProviderError: If loading fails.
    """
    try:
        from autocontext.training import HAS_MLX

        if not HAS_MLX:
            raise ImportError("MLX is not installed")

        import mlx.core as mx  # type: ignore[import-not-found]

        from autocontext.training.autoresearch.train import GPTModel, ModelConfig, load_checkpoint
    except ImportError as exc:
        raise ProviderError(
            f"MLX dependencies not available: {exc}. Install with: uv sync --group dev --extra mlx"
        ) from exc

    # Load config
    import json

    config_path = model_dir / "config.json"
    if not config_path.exists():
        raise ProviderError(
            f"Model config not found: {config_path}. "
            "Generate an inference bundle with save_inference_bundle()."
        )

    with open(config_path, encoding="utf-8") as f:
        raw_config = json.load(f)

    cfg = ModelConfig(**{k: v for k, v in raw_config.items() if hasattr(ModelConfig, k)})

    # Load model weights
    weights_path = _resolve_weights_path(model_dir)
    model = GPTModel(cfg)
    load_checkpoint(model, weights_path)
    mx.eval(model.parameters())

    # Load tokenizer
    tokenizer = _load_tokenizer(model_dir)

    return model, tokenizer


def _load_tokenizer(model_dir: Path) -> Any:
    """Load tokenizer from model directory.

    Tries tokenizer.json first, falls back to training a new one from
    the model config.
    """
    import json

    try:
        import tiktoken  # type: ignore[import-not-found]

        from autocontext.training.autoresearch.prepare import (
            _BPE_PAT,
            BASE_VOCAB_SIZE,
            AutoresearchTokenizer,
            build_special_tokens,
        )
    except ImportError as exc:
        raise ProviderError(f"Tokenizer dependencies not available: {exc}") from exc

    tokenizer_path = model_dir / "tokenizer.json"
    if not tokenizer_path.exists():
        raise ProviderError(f"Tokenizer not found: {tokenizer_path}")

    with open(tokenizer_path, encoding="utf-8") as f:
        tok_data = json.load(f)

    # If the tokenizer file contains mergeable_ranks, build a tiktoken encoding
    mergeable_ranks = tok_data.get("mergeable_ranks")
    if mergeable_ranks is not None:
        # Decode base64 ranks back to bytes
        import base64

        decoded_ranks = {base64.b64decode(k): v for k, v in mergeable_ranks.items()}
        base_vocab_size = int(tok_data.get("base_vocab_size", BASE_VOCAB_SIZE))
        pat_str = str(tok_data.get("pat_str", _BPE_PAT))
        special_tokens = build_special_tokens(base_vocab_size)
        enc = tiktoken.Encoding(
            name="mts_mlx_provider",
            pat_str=pat_str,
            mergeable_ranks=decoded_ranks,
            special_tokens=special_tokens,
        )
        return AutoresearchTokenizer(enc, base_vocab_size=base_vocab_size)

    # Fallback: return a simple mock-compatible tokenizer for testing
    raise ProviderError(f"Unsupported tokenizer format in {tokenizer_path}")


class MLXProvider(LLMProvider):
    """Provider using a locally-trained MLX model for strategy generation.

    Loads a GPT model from a safetensors checkpoint and generates text via
    autoregressive sampling with temperature control.
    """

    def __init__(
        self,
        model_path: str,
        temperature: float = 0.8,
        max_tokens: int = 512,
    ) -> None:
        model_dir = Path(model_path)
        if not model_dir.exists():
            raise ProviderError(f"Model path does not exist: {model_path}")
        if not (model_dir / "config.json").exists():
            raise ProviderError(f"Model config not found: {model_dir / 'config.json'}")
        _resolve_weights_path(model_dir)

        self._model_path = model_path
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._model, self._tokenizer = _load_model_and_tokenizer(model_dir)

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> CompletionResult:
        """Generate a completion using the local MLX model.

        The system and user prompts are concatenated.  ``temperature`` and
        ``max_tokens`` from the call override the instance defaults.
        """
        effective_temp = temperature if temperature > 0 else self._temperature
        effective_max = min(max_tokens, self._max_tokens) if max_tokens != 4096 else self._max_tokens

        prompt = f"{system_prompt}\n{user_prompt}" if system_prompt else user_prompt

        try:
            text = self._generate(prompt, temperature=effective_temp, max_tokens=effective_max)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"MLX generation error: {exc}") from exc

        return CompletionResult(
            text=text,
            model=model or self._model_path,
        )

    def _generate(self, prompt: str, *, temperature: float, max_tokens: int) -> str:
        """Run autoregressive sampling on the prompt.

        Args:
            prompt: Input text to condition generation on.
            temperature: Sampling temperature (higher = more diverse).
            max_tokens: Maximum number of new tokens to generate.

        Returns:
            Decoded output text.
        """
        prompt_tokens = self._tokenizer.encode(prompt)
        all_tokens = self._sample_tokens(prompt_tokens, temperature=temperature, max_tokens=max_tokens)
        generated_tokens = all_tokens[len(prompt_tokens):]
        end_token_id = getattr(self._tokenizer, "end_token_id", None)
        if generated_tokens and end_token_id is not None and generated_tokens[-1] == end_token_id:
            generated_tokens = generated_tokens[:-1]
        decoded: str = self._tokenizer.decode(generated_tokens)
        return decoded

    def _sample_tokens(
        self,
        prompt_tokens: list[int],
        *,
        temperature: float,
        max_tokens: int,
    ) -> list[int]:
        """Autoregressive token sampling loop.

        Args:
            prompt_tokens: Encoded prompt token IDs.
            temperature: Sampling temperature.
            max_tokens: Maximum new tokens to generate.

        Returns:
            List of all tokens (prompt + generated).
        """
        import mlx.core as mx  # type: ignore[import-not-found]

        tokens = list(prompt_tokens)
        seq_len = int(self._model.cfg.seq_len)
        end_token_id = getattr(self._tokenizer, "end_token_id", None)

        for _ in range(max_tokens):
            window = tokens[-seq_len:]
            x = mx.array([window], dtype=mx.int32)
            logits = self._model(x)
            next_logits = logits[:, -1, :]

            if temperature > 0:
                # Temperature-scaled sampling
                scaled = next_logits / temperature
                probs = mx.softmax(scaled, axis=-1)
                next_token = int(mx.random.categorical(mx.log(probs + 1e-10)).item())
            else:
                # Greedy decoding
                next_token = int(mx.argmax(next_logits, axis=-1).item())

            tokens.append(next_token)

            if end_token_id is not None and next_token == end_token_id:
                break

        return tokens

    def default_model(self) -> str:
        return self._model_path

    @property
    def name(self) -> str:
        return "mlx"
