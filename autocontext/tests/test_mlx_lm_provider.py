"""MLXLMProvider serving seams (CI-safe: the mlx-lm load/generate is gated, so these cover
the non-MLX logic -- chat-prompt assembly, adapter-path validation, and the injection seam)."""

from __future__ import annotations

import pytest

from autocontext.providers.base import ProviderError
from autocontext.providers.mlx_lm_provider import MLXLMProvider, format_mlxlm_prompt


class _StubTokenizer:
    """Records the messages passed to apply_chat_template and echoes a rendered string."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def apply_chat_template(self, messages, *, add_generation_prompt, tokenize):
        self.calls.append({"messages": messages, "add_generation_prompt": add_generation_prompt, "tokenize": tokenize})
        return f"<RENDERED:{messages[0]['content']}>"


def test_format_prompt_combines_system_and_user_into_one_user_turn() -> None:
    tok = _StubTokenizer()
    out = format_mlxlm_prompt(tok, "be terse", "solve x")
    assert out == "<RENDERED:be terse\n\nsolve x>"
    call = tok.calls[-1]
    assert call["messages"] == [{"role": "user", "content": "be terse\n\nsolve x"}]
    assert call["add_generation_prompt"] is True and call["tokenize"] is False


def test_format_prompt_user_only_when_no_system() -> None:
    tok = _StubTokenizer()
    format_mlxlm_prompt(tok, "", "just the user part")
    assert tok.calls[-1]["messages"][0]["content"] == "just the user part"


def test_missing_adapter_path_raises_before_any_mlx_load() -> None:
    # The adapter-existence check runs before the gated mlx-lm import, so this is CI-safe.
    with pytest.raises(ProviderError, match="adapter path does not exist"):
        MLXLMProvider("Qwen/Qwen2.5-1.5B-Instruct", adapter_path="/no/such/adapter/dir")


def test_injection_seam_skips_load_and_exposes_model_id() -> None:
    provider = MLXLMProvider("Org/Model-1.5B", adapter_path=None, _loaded=(object(), _StubTokenizer()))
    assert provider.default_model() == "Org/Model-1.5B"


def test_score_conditioned_reapplies_top_bucket_quality_directive() -> None:
    """A score-conditioned adapter must be served with the same top-bucket quality prefix it
    was assessed under, else the prompt contract differs and generation can regress."""
    conditioned = MLXLMProvider("M", score_conditioned=True, _loaded=(object(), _StubTokenizer()))
    prefixed = conditioned._quality_prefixed("do the task")
    assert prefixed.startswith("Target quality:")
    assert prefixed.endswith("do the task")

    plain = MLXLMProvider("M", score_conditioned=False, _loaded=(object(), _StubTokenizer()))
    assert plain._quality_prefixed("do the task") == "do the task"
