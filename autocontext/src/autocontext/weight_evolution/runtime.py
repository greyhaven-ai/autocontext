"""Immutable inference image identity and observed dependency provenance."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import re
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from autocontext.runtime_images import require_pinned_runtime_image
from autocontext.weight_evolution.models import FrozenModel, _unique_object

REQUIRED_PACKAGES = ("vllm", "torch", "transformers", "numpy", "safetensors", "tokenizers", "pydantic")


class PackageVersion(FrozenModel):
    name: Literal["vllm", "torch", "transformers", "numpy", "safetensors", "tokenizers", "pydantic"]
    version: str = Field(min_length=1, max_length=100)

    @field_validator("version")
    @classmethod
    def exact_version(cls, value: str) -> str:
        if re.fullmatch(r"[0-9][A-Za-z0-9.+_-]*", value) is None:
            raise ValueError("package pins must be exact versions")
        return value


class RuntimeLock(FrozenModel):
    schema_version: Literal[1]
    image: str
    platform: Literal["linux/amd64"] = "linux/amd64"
    python_version: str
    packages: tuple[PackageVersion, ...] = Field(min_length=1)
    fidelity_notes: tuple[str, ...] = ()

    @field_validator("image")
    @classmethod
    def pinned_image(cls, value: str) -> str:
        return require_pinned_runtime_image(value)

    @field_validator("python_version")
    @classmethod
    def python_minor(cls, value: str) -> str:
        if re.fullmatch(r"3\.[0-9]+(?:\.[0-9]+)?", value) is None:
            raise ValueError("Python pin must be a minor or exact patch version")
        return value

    @model_validator(mode="after")
    def unique_packages(self) -> Self:
        names = [item.name for item in self.packages]
        if len(set(names)) != len(names) or "vllm" not in names:
            raise ValueError("runtime must pin vllm and cannot duplicate packages")
        return self

    @property
    def digest(self) -> str:
        data = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(data.encode()).hexdigest()


def load_runtime_lock(path: Path) -> RuntimeLock:
    with path.open("rb") as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("runtime lock exceeds 64 KiB")
    json.loads(data, object_pairs_hook=_unique_object)
    return RuntimeLock.model_validate_json(data)


def check_installed_runtime(runtime: RuntimeLock) -> dict[str, Any]:
    """Check declared pins and record exact packages captured by the image digest.

    The controller verifies the actual Docker image. An environment variable or
    self-reported image name inside a process is not image attestation.
    """
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "AMD64"}:
        raise ValueError("the pinned inference runtime requires Linux amd64")
    actual_python = platform.python_version()
    expected = runtime.python_version.split(".")
    if actual_python.split(".")[:len(expected)] != expected:
        raise ValueError(f"Python runtime mismatch: expected {runtime.python_version}, found {actual_python}")
    versions: dict[str, str] = {}
    for name in REQUIRED_PACKAGES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise ValueError(f"required runtime package is absent: {name}") from exc
    for pin in runtime.packages:
        if versions[pin.name] != pin.version:
            raise ValueError(f"runtime package mismatch for {pin.name}: expected {pin.version}, found {versions[pin.name]}")
    return {"python_version": actual_python, "packages": versions, "platform": runtime.platform}
