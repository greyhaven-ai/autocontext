"""MLXLMProvider -- serve an mlx-lm model (optionally base + LoRA adapter) as a provider.

This is the serving counterpart to the `mlxlm` / `opd` / `trl` training backends, which emit
mlx-lm models plus LoRA adapter directories (loaded via ``mlx_lm.load(base, adapter_path=...)``).
``MLXProvider`` only serves the from-scratch autoresearch GPT; this provider serves the capable
pretrained-instruct fine-tunes so a harness-trained adapter can be the agent (the recursive
loop for capable models). Mirrors the load+generate path already used by ``_assess_mlxlm``.

All mlx-lm imports are lazy/guarded so this module imports without MLX (Linux/CI); only
constructing/running the provider needs mlx-lm (Apple Silicon).
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any

from autocontext.providers.base import CompletionResult, LLMProvider, ProviderError

logger = logging.getLogger(__name__)

HAS_MLXLM = importlib.util.find_spec("mlx_lm") is not None


def format_mlxlm_prompt(tokenizer: Any, system_prompt: str, user_prompt: str) -> str:
    """Render a chat prompt for an mlx-lm instruct model via its chat template.

    System and user text are combined into a single user turn (the agent roles pass a
    system + user prompt); ``add_generation_prompt`` leaves the model positioned to answer.
    """
    content = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt
    rendered = tokenizer.apply_chat_template(
        [{"role": "user", "content": content}],
        add_generation_prompt=True,
        tokenize=False,
    )
    return str(rendered)


class MLXLMProvider(LLMProvider):
    """Provider over a local mlx-lm model, optionally with a LoRA adapter."""

    def __init__(
        self,
        model: str,
        *,
        adapter_path: str | None = None,
        temperature: float = 0.8,
        max_tokens: int = 512,
        score_conditioned: bool = False,
        _loaded: tuple[Any, Any] | None = None,
    ) -> None:
        self._model_id = model
        self._adapter_path = adapter_path
        self._temperature = temperature
        self._max_tokens = max_tokens
        # Score-conditioned mlxlm adapters were trained + assessed with a top-bucket quality
        # directive in the prompt (registry data_stats records the flag); reapply it at serving
        # time so the served prompt matches the contract the adapter passed assessment under.
        self._score_conditioned = score_conditioned
        if _loaded is not None:  # test/injection seam: skip the mlx-lm load
            self._model, self._tokenizer = _loaded
            return
        if adapter_path is not None and not Path(adapter_path).exists():
            raise ProviderError(f"adapter path does not exist: {adapter_path}")
        if not HAS_MLXLM:
            raise ProviderError("mlx-lm is required for MLXLMProvider; install with: uv pip install mlx-lm")
        from mlx_lm import load

        loaded = load(model, adapter_path=adapter_path) if adapter_path else load(model)
        self._model, self._tokenizer = loaded[0], loaded[1]

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> CompletionResult:
        from mlx_lm import generate
        from mlx_lm.sample_utils import make_sampler

        effective_temp = temperature if temperature > 0 else self._temperature
        effective_max = min(max_tokens, self._max_tokens) if max_tokens != 4096 else self._max_tokens
        prompt = format_mlxlm_prompt(self._tokenizer, system_prompt, self._quality_prefixed(user_prompt))
        try:
            text = generate(
                self._model,
                self._tokenizer,
                prompt=prompt,
                max_tokens=effective_max,
                sampler=make_sampler(temp=max(float(effective_temp), 0.0)),
                verbose=False,
            )
        except Exception as exc:
            logger.debug("providers.mlx_lm_provider: caught Exception", exc_info=True)
            raise ProviderError(f"MLX-LM generation error: {exc}") from exc
        return CompletionResult(text=text, model=model or self._model_id)

    def _quality_prefixed(self, user_prompt: str) -> str:
        """Prepend the top-bucket quality directive for score-conditioned adapters.

        Reuses the training backend's exact ``_quality_prefix`` text (the same prefix
        ``_assess_mlxlm`` applied), so the served prompt matches the assessed one.
        """
        if not self._score_conditioned:
            return user_prompt
        from autocontext.training.autoresearch.mlxlm_backend import NUM_QUALITY_BUCKETS, _quality_prefix

        return _quality_prefix(NUM_QUALITY_BUCKETS - 1, NUM_QUALITY_BUCKETS) + user_prompt

    def default_model(self) -> str:
        return self._model_id
