from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from autocontext.weight_evolution import runtime

RUNTIME_LOCK = Path(__file__).resolve().parents[2] / "examples/weight_evolution/qwen_reproduction/runtime.lock.json"
EXPECTED_IMAGE = (
    "vllm/vllm-openai:v0.32.0@sha256:"
    "6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9"
)


def _data() -> dict[str, Any]:
    return json.loads(RUNTIME_LOCK.read_text())


def _parse(data: dict[str, Any]) -> runtime.RuntimeLock:
    return runtime.RuntimeLock.model_validate_json(json.dumps(data))


@pytest.fixture
def observed_environment(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    # Synthetic installed versions exercise provenance capture, not claims about
    # the registry image's currently unobserved Python/package metadata.
    versions = {
        "vllm": "0.32.0",
        "torch": "2.13.0+cu130",
        "transformers": "5.17.0",
        "numpy": "2.4.3",
        "safetensors": "0.6.2",
        "tokenizers": "0.23.2",
        "pydantic": "2.12.0",
    }
    monkeypatch.setattr(runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(runtime.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(runtime.platform, "python_version", lambda: "3.12.11")
    monkeypatch.setattr(runtime.importlib.metadata, "version", versions.__getitem__)
    return versions


def test_example_runtime_binds_verified_amd64_image() -> None:
    lock = runtime.load_runtime_lock(RUNTIME_LOCK)
    assert lock.image == EXPECTED_IMAGE
    assert lock.platform == "linux/amd64"
    assert lock.python_version == "3.12"
    assert [(pin.name, pin.version) for pin in lock.packages] == [("vllm", "0.32.0")]
    assert lock.digest == _parse(lock.to_dict()).digest


@pytest.mark.parametrize("image", [
    "vllm/vllm-openai:latest",
    "vllm/vllm-openai:v0.32.0",
    "vllm/vllm-openai@sha256:" + "a" * 63,
    "vllm/vllm-openai@sha256:" + "A" * 64,
    "vllm/vllm-openai@sha256:" + "g" * 64,
    "vllm/vllm-openai@sha512:" + "a" * 64,
    EXPECTED_IMAGE + "\n",
    EXPECTED_IMAGE + " --privileged",
])
def test_runtime_rejects_mutable_or_ambiguous_image(image: str) -> None:
    data = _data()
    data["image"] = image
    with pytest.raises(ValidationError, match="immutable"):
        _parse(data)


@pytest.mark.parametrize("version", ["", ">=0.32.0", "0.32.*", "0.32.0; echo test", "0.32.0\n", "latest"])
def test_runtime_rejects_nonexact_package_pins(version: str) -> None:
    data = _data()
    data["packages"][0]["version"] = version
    with pytest.raises(ValidationError):
        _parse(data)


@pytest.mark.parametrize("name", ["../../torch", "unapproved-package", "torch; echo test", "Torch"])
def test_runtime_rejects_unknown_package_names(name: str) -> None:
    data = _data()
    data["packages"][0]["name"] = name
    with pytest.raises(ValidationError):
        _parse(data)


@pytest.mark.parametrize("packages", [
    [],
    [{"name": "torch", "version": "2.13.0"}],
    [{"name": "vllm", "version": "0.32.0"}, {"name": "vllm", "version": "0.31.0"}],
])
def test_runtime_rejects_missing_engine_or_duplicate_package_identity(packages: list[dict[str, str]]) -> None:
    data = _data()
    data["packages"] = packages
    with pytest.raises(ValidationError):
        _parse(data)


@pytest.mark.parametrize("python", ["3", "3.12.*", ">=3.12", "3.12\n", "3.12.1.2", "2.7"])
def test_runtime_rejects_ambiguous_python_version(python: str) -> None:
    data = _data()
    data["python_version"] = python
    with pytest.raises(ValidationError):
        _parse(data)


@pytest.mark.parametrize("payload", [
    '{"schema_version":1,"schema_version":1}',
    '{"packages":[{"name":"vllm","name":"torch","version":"0.32.0"}]}',
])
def test_runtime_rejects_duplicate_json_keys(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "runtime.json"
    path.write_text(payload)
    with pytest.raises(ValueError, match="duplicate JSON key"):
        runtime.load_runtime_lock(path)


def test_runtime_rejects_oversized_lock(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    path.write_bytes(b" " * 65537)
    with pytest.raises(ValueError, match="64 KiB"):
        runtime.load_runtime_lock(path)


def test_observed_receipt_captures_exact_environment(observed_environment: dict[str, str]) -> None:
    lock = runtime.load_runtime_lock(RUNTIME_LOCK)
    receipt = runtime.check_installed_runtime(lock)
    assert receipt == {
        "python_version": "3.12.11",
        "packages": observed_environment,
        "platform": "linux/amd64",
    }
    # The worker does not claim that self-reported process metadata attests a
    # Docker image; that identity must come from the trusted controller.
    assert "image" not in receipt


@pytest.mark.parametrize(("system", "machine"), [("Darwin", "arm64"), ("Linux", "aarch64"), ("Windows", "AMD64")])
def test_runtime_rejects_wrong_host_platform(
    observed_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch, system: str, machine: str,
) -> None:
    monkeypatch.setattr(runtime.platform, "system", lambda: system)
    monkeypatch.setattr(runtime.platform, "machine", lambda: machine)
    with pytest.raises(ValueError, match="Linux amd64"):
        runtime.check_installed_runtime(runtime.load_runtime_lock(RUNTIME_LOCK))


@pytest.mark.parametrize(("expected", "actual"), [("3.12", "3.13.1"), ("3.12.10", "3.12.11")])
def test_runtime_rejects_python_mismatch(
    observed_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch, expected: str, actual: str,
) -> None:
    data = _data()
    data["python_version"] = expected
    monkeypatch.setattr(runtime.platform, "python_version", lambda: actual)
    with pytest.raises(ValueError, match="Python runtime mismatch"):
        runtime.check_installed_runtime(_parse(data))


def test_runtime_rejects_engine_mismatch(observed_environment: dict[str, str]) -> None:
    observed_environment["vllm"] = "0.31.0"
    with pytest.raises(ValueError, match="package mismatch for vllm"):
        runtime.check_installed_runtime(runtime.load_runtime_lock(RUNTIME_LOCK))


def test_additional_exact_package_pin_is_enforced(observed_environment: dict[str, str]) -> None:
    data = _data()
    data["packages"].append({"name": "torch", "version": "2.13.0+cu130"})
    lock = _parse(data)
    runtime.check_installed_runtime(lock)
    observed_environment["torch"] = "2.13.0+cu129"
    with pytest.raises(ValueError, match="package mismatch for torch"):
        runtime.check_installed_runtime(lock)


@pytest.mark.parametrize("missing", runtime.REQUIRED_PACKAGES)
def test_runtime_rejects_missing_required_package(
    observed_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch, missing: str,
) -> None:
    def installed(name: str) -> str:
        if name == missing:
            raise importlib.metadata.PackageNotFoundError(name)
        return observed_environment[name]

    monkeypatch.setattr(runtime.importlib.metadata, "version", installed)
    with pytest.raises(ValueError, match=f"package is absent: {missing}"):
        runtime.check_installed_runtime(runtime.load_runtime_lock(RUNTIME_LOCK))


def test_runtime_identity_changes_with_additional_pin() -> None:
    data = _data()
    original = _parse(data)
    data["packages"].append({"name": "torch", "version": "2.13.0+cu130"})
    assert original.digest != _parse(data).digest
