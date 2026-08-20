"""Shared immutable runtime image identities and validation."""

from __future__ import annotations

import re

PINNED_PYTHON_RUNTIME_IMAGE = (
    "python:3.11.10-slim-bookworm@sha256:840e180ebcc6e5c8efab209c43f5e40fd2af98cb49db5c7103c90539c56bb30e"
)
_PINNED_IMAGE_PATTERN = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")


def require_pinned_runtime_image(image: str) -> str:
    """Reject mutable or malformed container image references."""

    if not _PINNED_IMAGE_PATTERN.fullmatch(image):
        raise ValueError("remote runtime image must use an immutable @sha256 digest")
    return image


__all__ = ["PINNED_PYTHON_RUNTIME_IMAGE", "require_pinned_runtime_image"]
