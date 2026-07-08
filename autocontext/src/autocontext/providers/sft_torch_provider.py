"""SftTorchProvider -- serve a torch/peft SFT LoRA adapter (base model + adapter) as a provider.

The serving counterpart to the TRL ``sft`` training backend (see ``training/autoresearch/
sft_backend.py``), which fine-tunes a transformers base model and writes a peft LoRA adapter
directory. ``MLXLMProvider`` serves the mlx-lm adapters (Apple Silicon); this provider serves the
torch/peft adapters (the ``cuda`` extra), so a harness-trained SFT adapter can drive an agent role.

All torch / transformers / peft imports are lazy inside ``__init__`` so this module imports without
torch (Linux/CI). Constructing the provider in a torch-absent environment raises ``ImportError``,
which the serving resolver caller catches to fall back to the frontier client.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from autocontext.providers.base import CompletionResult, ProviderError

logger = logging.getLogger(__name__)


class SftTorchProvider:
    """Provider over a local transformers base model plus an optional peft LoRA adapter."""

    def __init__(
        self,
        model: str,
        *,
        adapter_path: str | None = None,
        temperature: float = 0.8,
        max_tokens: int = 512,
    ) -> None:
        self._model_id = model
        self._adapter_path = adapter_path
        self._temperature = temperature
        self._max_tokens = max_tokens
        # Lazy + guarded: importing torch/transformers/peft here (not at module import time) keeps
        # this an optional (``cuda`` extra) dependency. A torch-absent environment raises ImportError
        # right here, which the resolver caller catches so it falls back to the frontier client.
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore[import-not-found]

        self._torch = torch
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        if adapter_path is not None and not Path(adapter_path).exists():
            raise ProviderError(f"adapter path does not exist: {adapter_path}")
        self._tokenizer = AutoTokenizer.from_pretrained(model)
        base = AutoModelForCausalLM.from_pretrained(model)
        if adapter_path is not None:
            from peft import PeftModel  # type: ignore[import-not-found]

            base = PeftModel.from_pretrained(base, adapter_path)
        self._model = base.to(self._device)

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 512,
    ) -> CompletionResult:
        """Generate a completion for ``system + prompt`` and return text + token-count usage."""
        text = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt
        effective_temp = temperature if temperature > 0 else self._temperature
        effective_max = max_tokens if max_tokens > 0 else self._max_tokens
        try:
            inputs = self._tokenizer(text, return_tensors="pt").to(self._device)
            input_len = int(inputs["input_ids"].shape[1])
            gen_kwargs: dict[str, Any] = {"max_new_tokens": effective_max}
            if effective_temp > 0:
                gen_kwargs["do_sample"] = True
                gen_kwargs["temperature"] = float(effective_temp)
            else:
                gen_kwargs["do_sample"] = False
            with self._torch.no_grad():
                output = self._model.generate(**inputs, **gen_kwargs)
            generated = output[0][input_len:]
            decoded = self._tokenizer.decode(generated, skip_special_tokens=True)
            output_len = int(generated.shape[0])
        except Exception as exc:
            logger.debug("providers.sft_torch_provider: caught Exception", exc_info=True)
            raise ProviderError(f"SFT torch generation error: {exc}") from exc
        return CompletionResult(
            text=str(decoded).strip(),
            model=self._model_id,
            usage={"input_tokens": input_len, "output_tokens": output_len},
        )

    def default_model(self) -> str:
        return self._model_id
