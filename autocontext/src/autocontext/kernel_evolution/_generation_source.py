"""Exact generated-source validation."""

from __future__ import annotations

import ast

from autocontext.kernel_evolution._generation_errors import KernelGenerationValidationError

_TRUNCATED_STOP_REASONS = frozenset({"max_tokens", "length", "incomplete", "content_filter"})


def validate_kernel_source(
    source: str,
    *,
    source_suffix: str,
    entrypoint: str,
    stop_reason: str | None,
    max_source_bytes: int,
) -> str:
    """Return exact bytes only when the response is a complete source artifact."""
    if stop_reason is not None and stop_reason.lower().strip() in _TRUNCATED_STOP_REASONS:
        raise KernelGenerationValidationError(f"provider response was truncated (stop_reason={stop_reason})")
    if not source.strip():
        raise KernelGenerationValidationError("provider returned empty kernel source")
    if "```" in source:
        raise KernelGenerationValidationError("provider returned Markdown fences instead of exact source")
    encoded = source.encode("utf-8")
    if len(encoded) > max_source_bytes:
        raise KernelGenerationValidationError(
            f"provider response exceeds the {max_source_bytes}-byte source limit"
        )
    if source_suffix == ".py":
        try:
            tree = ast.parse(source, filename="<generated-kernel>", mode="exec")
        except SyntaxError as exc:
            raise KernelGenerationValidationError(
                f"provider returned malformed Python source: {exc.msg} at line {exc.lineno}"
            ) from exc
        definitions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        if entrypoint not in definitions:
            raise KernelGenerationValidationError(
                f"provider response does not define required top-level entrypoint {entrypoint!r}"
            )
    return source


__all__ = ["validate_kernel_source"]
