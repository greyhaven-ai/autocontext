from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from autocontext.weight_evolution.assets import AssetError, fetch_assets, select_assets, verify_assets
from autocontext.weight_evolution.models import (
    MODEL_REVISION,
    SOURCE_REVISION,
    AssetPin,
    ContractError,
    ReproductionLock,
    load_reproduction_lock,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/weight_evolution/qwen_reproduction"
LOCK = EXAMPLE / "reproduction.lock.json"


def _read_lock() -> dict[str, Any]:
    return json.loads(LOCK.read_text())


def _parse(data: dict[str, Any]) -> ReproductionLock:
    return ReproductionLock.model_validate_json(json.dumps(data))


@pytest.fixture
def fixture_assets() -> tuple[ReproductionLock, dict[str, bytes]]:
    """Tiny authored assets with the real contract; never download upstream code."""
    data = _read_lock()
    contents: dict[str, bytes] = {}
    for asset in data["assets"]:
        content = f"inert fixture: {asset['asset_id']}\n".encode()
        if asset["asset_id"] == "patch_manifest":
            content = json.dumps({
                "tensors": data["patch"]["tensors"],
                "changed_tensors": 2,
                "changed_entries_total": 130188,
            }).encode()
        if asset["asset_id"] == "apply_edit_source":
            content = b"raise RuntimeError('upstream source must never execute')\n"
        asset["sha256"] = hashlib.sha256(content).hexdigest()
        asset["size_bytes"] = len(content)
        contents[asset["url"]] = content
    return _parse(data), contents


def _materialize(lock: ReproductionLock, contents: dict[str, bytes], root: Path) -> None:
    for asset in select_assets(lock):
        target = root / asset.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents[asset.url])


def test_published_contract_pins_and_no_network() -> None:
    with patch("urllib.request.urlopen", side_effect=AssertionError("network access")):
        lock = load_reproduction_lock(LOCK)
    assert lock.source.revision == SOURCE_REVISION
    assert lock.model.revision == MODEL_REVISION
    assert lock.patch.changed_entries == 130188
    assert lock.patch.touched_entries == 131072
    assert lock.evaluation.samples_per_problem.math500 == 4
    assert sum(asset.size_bytes for asset in select_assets(lock)) == 472000
    assert not any(asset.role == "model_weights" for asset in select_assets(lock))


def test_nested_protocol_is_immutable() -> None:
    lock = load_reproduction_lock(LOCK)
    with pytest.raises(ValidationError, match="frozen"):
        lock.evaluation.samples_per_problem.math500 = 4000  # type: ignore[misc,assignment]
    with pytest.raises(ValidationError, match="frozen"):
        lock.model.revision = "a" * 40  # type: ignore[misc,assignment]


@pytest.mark.parametrize(("section", "key", "value"), [
    ("source", "revision", "main"),
    ("model", "revision", "a" * 40),
    ("model", "dtype", "float16"),
    ("evaluation", "temperature", 0.7),
    ("evaluation", "thinking", False),
    ("evaluation", "math_prompt_suffix", "Reason carefully"),
    ("evaluation", "math_metric", "pass_at_k"),
    ("evaluation", "ifeval_metric", "loose_prompt_accuracy"),
    ("evaluation", "samples_per_problem", {"math500": 1, "gsm8k": 2, "ifeval": 2}),
    ("evaluation", "max_new_tokens", 4096),
    ("patch", "changed_entries", 131072),
    ("patch", "delta_asset_id", "source_readme"),
    ("patch", "manifest_asset_id", "evaluate_source"),
])
def test_reject_protocol_drift(section: str, key: str, value: object) -> None:
    data = _read_lock()
    data[section][key] = value
    with pytest.raises(ValidationError):
        _parse(data)


@pytest.mark.parametrize("mutation", ["remove_template", "optional_template", "duplicate_id", "duplicate_path", "bad_hash"])
def test_reject_missing_or_ambiguous_asset_pins(mutation: str) -> None:
    data = _read_lock()
    if mutation == "remove_template":
        data["assets"] = [x for x in data["assets"] if x["asset_id"] != "chat_template"]
    elif mutation == "optional_template":
        next(x for x in data["assets"] if x["asset_id"] == "chat_template")["required"] = False
    elif mutation == "duplicate_id":
        data["assets"].append(copy.deepcopy(data["assets"][0]))
    elif mutation == "duplicate_path":
        data["assets"][-1]["relative_path"] = data["assets"][0]["relative_path"].upper()
    else:
        data["assets"][0]["sha256"] = "unverified"
    with pytest.raises(ValidationError):
        _parse(data)


@pytest.mark.parametrize("path", ["../x", "/tmp/x", "a/../b", "a//b", "a/./b", "a\\b", "a:\\b", "a\x00b"])
def test_reject_unsafe_destination(path: str) -> None:
    data = _read_lock()["assets"][0]
    data["relative_path"] = path
    with pytest.raises(ValidationError):
        AssetPin.model_validate(data)


@pytest.mark.parametrize("url", [
    "http://raw.githubusercontent.com/a", "https://example.com/x", "file:///tmp/x",
    "https://raw.githubusercontent.com/RikiyaT/lm_understand-logs/main/file.py",
    f"https://raw.githubusercontent.com/RikiyaT/lm_understand-logs/{SOURCE_REVISION}/../x",
    f"https://raw.githubusercontent.com/RikiyaT/lm_understand-logs/{SOURCE_REVISION}/%2e%2e/x",
])
def test_reject_unpinned_urls(url: str) -> None:
    data = _read_lock()["assets"][0]
    data["url"] = url
    with pytest.raises(ValidationError):
        AssetPin.model_validate(data)


def test_loader_rejects_duplicate_keys_and_oversized_lock(tmp_path: Path) -> None:
    path = tmp_path / "lock.json"
    path.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(ContractError, match="duplicate"):
        load_reproduction_lock(path)
    path.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ContractError, match="exceeds"):
        load_reproduction_lock(path)


def test_digest_canonical_and_asset_sensitive() -> None:
    data = _read_lock()
    lock = _parse(data)
    assert load_reproduction_lock(LOCK).digest == lock.digest
    data["assets"][0]["sha256"] = "0" * 64
    assert _parse(data).digest != lock.digest


def test_offline_verification_and_missing_assets(
    fixture_assets: tuple[ReproductionLock, dict[str, bytes]], tmp_path: Path,
) -> None:
    lock, contents = fixture_assets
    report = verify_assets(lock, tmp_path)
    assert not report.ok and all(item.status == "missing" for item in report.assets)
    _materialize(lock, contents, tmp_path)
    with patch("urllib.request.build_opener", side_effect=AssertionError("must stay offline")):
        report = verify_assets(lock, tmp_path)
    assert report.ok
    assert report.to_dict()["model_execution_performed"] is False
    assert report.fidelity_gaps == lock.fidelity_gaps


@pytest.mark.parametrize("mode", ["same_size_corruption", "oversized", "truncated"])
def test_byte_tampering_detected(
    fixture_assets: tuple[ReproductionLock, dict[str, bytes]], tmp_path: Path, mode: str,
) -> None:
    lock, contents = fixture_assets
    _materialize(lock, contents, tmp_path)
    asset = lock.assets[0]
    data = contents[asset.url]
    changed = b"!" + data[1:] if mode == "same_size_corruption" else data + b"!" if mode == "oversized" else data[:-1]
    (tmp_path / asset.relative_path).write_bytes(changed)
    report = verify_assets(lock, tmp_path)
    assert not report.ok and report.assets[0].status == "invalid"


def test_metadata_disagreement_with_verified_patch_manifest(
    fixture_assets: tuple[ReproductionLock, dict[str, bytes]], tmp_path: Path,
) -> None:
    lock, contents = fixture_assets
    _materialize(lock, contents, tmp_path)
    data = lock.to_dict()
    data["patch"]["tensors"][0]["base_sha256"] = "0" * 64
    report = verify_assets(_parse(data), tmp_path, asset_ids=["patch_manifest"])
    assert not report.ok and "disagree" in report.assets[0].detail


@pytest.mark.parametrize("ancestor", [False, True])
def test_symlink_assets_rejected(
    fixture_assets: tuple[ReproductionLock, dict[str, bytes]], tmp_path: Path, ancestor: bool,
) -> None:
    lock, contents = fixture_assets
    root, outside = tmp_path / "root", tmp_path / "outside"
    _materialize(lock, contents, outside)
    root.mkdir()
    if ancestor:
        (root / "exp8_math").symlink_to(outside / "exp8_math", target_is_directory=True)
    else:
        (root / "exp8_math").mkdir()
        (root / lock.assets[0].relative_path).symlink_to(outside / lock.assets[0].relative_path)
    report = verify_assets(lock, root, asset_ids=[lock.assets[0].asset_id])
    assert not report.ok and "symlink" in report.assets[0].detail


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFO")
def test_fifo_does_not_block_verification(
    fixture_assets: tuple[ReproductionLock, dict[str, bytes]], tmp_path: Path,
) -> None:
    lock, _ = fixture_assets
    path = tmp_path / lock.assets[0].relative_path
    path.parent.mkdir(parents=True)
    os.mkfifo(path)
    report = verify_assets(lock, tmp_path, asset_ids=[lock.assets[0].asset_id])
    assert report.assets[0].status == "invalid"


def test_bounded_fetch_then_idempotent_offline_reuse(
    fixture_assets: tuple[ReproductionLock, dict[str, bytes]], tmp_path: Path,
) -> None:
    lock, contents = fixture_assets
    called: list[str] = []

    class Opener:
        def open(self, request: Any, *, timeout: float) -> io.BytesIO:
            called.append(request.full_url)
            assert 0 < timeout <= 300
            return io.BytesIO(contents[request.full_url])

    with patch("urllib.request.build_opener", return_value=Opener()):
        report = fetch_assets(lock, tmp_path)
    assert report.ok and len(called) == len(select_assets(lock))
    with patch("urllib.request.build_opener", side_effect=AssertionError("unnecessary download")):
        assert fetch_assets(lock, tmp_path).ok
    assert not list(tmp_path.rglob("*.partial"))


@pytest.mark.parametrize("oversized", [False, True])
def test_bad_download_never_published(
    fixture_assets: tuple[ReproductionLock, dict[str, bytes]], tmp_path: Path, oversized: bool,
) -> None:
    lock, contents = fixture_assets
    asset = lock.assets[0]
    body = contents[asset.url] + b"extra" if oversized else b"corrupt"

    class Opener:
        def open(self, request: Any, *, timeout: float) -> io.BytesIO:
            return io.BytesIO(body)

    with patch("urllib.request.build_opener", return_value=Opener()), pytest.raises(AssetError):
        fetch_assets(lock, tmp_path, asset_ids=[asset.asset_id])
    assert not (tmp_path / asset.relative_path).exists()
    assert not list(tmp_path.rglob("*.partial"))


def test_budget_and_selection_fail_before_network(tmp_path: Path) -> None:
    lock = load_reproduction_lock(LOCK)
    with patch("urllib.request.build_opener", side_effect=AssertionError("network access")):
        with pytest.raises(AssetError, match="budget"):
            fetch_assets(lock, tmp_path, max_download_bytes=1)
        model_id = next(asset.asset_id for asset in lock.assets if asset.role == "model_weights")
        with pytest.raises(AssetError, match="budget"):
            fetch_assets(lock, tmp_path, asset_ids=[model_id])
        with pytest.raises(AssetError, match="unknown"):
            verify_assets(lock, tmp_path, asset_ids=["not_present"])
        with pytest.raises(AssetError, match="distinct"):
            select_assets(lock, [])


@pytest.mark.parametrize("budget", [True, -1, 0, float("nan"), float("inf"), 3.5])
def test_invalid_download_budget_rejected_before_io(tmp_path: Path, budget: Any) -> None:
    lock = load_reproduction_lock(LOCK)
    with patch("urllib.request.build_opener", side_effect=AssertionError("network access")):
        with pytest.raises(AssetError, match="budget"):
            fetch_assets(lock, tmp_path, max_download_bytes=budget)


def test_existing_corrupt_asset_is_preserved(
    fixture_assets: tuple[ReproductionLock, dict[str, bytes]], tmp_path: Path,
) -> None:
    lock, contents = fixture_assets
    _materialize(lock, contents, tmp_path)
    path = tmp_path / lock.assets[0].relative_path
    path.write_bytes(b"corrupt")
    with patch("urllib.request.build_opener", side_effect=AssertionError("network access")), pytest.raises(AssetError):
        fetch_assets(lock, tmp_path)
    assert path.read_bytes() == b"corrupt"


def test_cli_validates_and_reports_missing_assets(tmp_path: Path) -> None:
    script = str(EXAMPLE / "run.py")
    valid = subprocess.run([sys.executable, script, "validate-lock"], capture_output=True, text=True, check=False)
    assert valid.returncode == 0, valid.stderr
    payload = json.loads(valid.stdout)
    assert payload["ok"] and not payload["assets_verified"]
    missing = subprocess.run(
        [sys.executable, script, "verify-assets", "--asset-root", str(tmp_path)],
        capture_output=True, text=True, check=False,
    )
    assert missing.returncode == 1
    assert not json.loads(missing.stdout)["ok"]
