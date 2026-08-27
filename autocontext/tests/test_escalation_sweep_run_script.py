from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "escalation-sweep" / "run_sweep.sh"
HAS_JQ = shutil.which("jq") is not None


def _run_validation(identifier: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; validate_sweep_identifier "$2"',
            "bash",
            str(SCRIPT_PATH),
            identifier,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _resolve_target(workspaces_dir: Path | str, identifier: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; resolve_workspace_target "$2" "$3"',
            "bash",
            str(SCRIPT_PATH),
            str(workspaces_dir),
            identifier,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("identifier", ["A", "AC-1011", "safe_identifier", "a" * 128])
def test_sweep_identifier_accepts_conservative_filename_ids(identifier: str) -> None:
    result = _run_validation(identifier)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "identifier",
    [
        "",
        ".",
        "..",
        ".hidden",
        "artifact.json",
        "../victim",
        "nested/escape",
        r"nested\escape",
        "/absolute/path",
        r"C:\escape",
        "_leading",
        "-leading",
        "a" * 129,
    ],
)
def test_sweep_identifier_rejects_path_shaped_values(identifier: str) -> None:
    result = _run_validation(identifier)

    assert result.returncode != 0
    assert "invalid sweep identifier" in result.stderr


def test_workspace_target_resolves_to_exact_child(tmp_path: Path) -> None:
    workspaces_dir = tmp_path / "workspaces"
    workspaces_dir.mkdir()

    result = _resolve_target(workspaces_dir, "AC-1011")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(workspaces_dir.resolve() / "AC-1011")


def test_workspace_target_rejects_broad_root() -> None:
    result = _resolve_target("/", "safe-id")

    assert result.returncode != 0
    assert "refusing unsafe workspace root" in result.stderr


def test_workspace_target_rejects_symlink_escape(tmp_path: Path) -> None:
    workspaces_dir = tmp_path / "workspaces"
    outside_dir = tmp_path / "outside"
    workspaces_dir.mkdir()
    outside_dir.mkdir()
    sentinel = outside_dir / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    (workspaces_dir / "safe-id").symlink_to(outside_dir, target_is_directory=True)

    result = _resolve_target(workspaces_dir, "safe-id")

    assert result.returncode != 0
    assert "refusing symlink workspace target" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(not HAS_JQ, reason="run_sweep.sh requires jq")
def test_invalid_manifest_id_fails_before_cleanup(tmp_path: Path) -> None:
    sweep_root = tmp_path / "sweep"
    results_dir = sweep_root / "results"
    victim_dir = sweep_root / "victim"
    victim_dir.mkdir(parents=True)
    sentinel = victim_dir / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps([{"identifier": "../victim", "description": "must not run"}]),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), str(manifest), str(results_dir)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "invalid sweep identifier" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(not HAS_JQ, reason="run_sweep.sh requires jq")
def test_existing_workspace_symlink_fails_before_cleanup(tmp_path: Path) -> None:
    sweep_root = tmp_path / "sweep"
    results_dir = sweep_root / "results"
    workspaces_dir = sweep_root / "workspaces"
    outside_dir = tmp_path / "outside"
    workspaces_dir.mkdir(parents=True)
    outside_dir.mkdir()
    sentinel = outside_dir / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    (workspaces_dir / "safe-id").symlink_to(outside_dir, target_is_directory=True)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps([{"identifier": "safe-id", "description": "must not run"}]),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), str(manifest), str(results_dir)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "refusing symlink workspace target" in result.stderr
    assert (workspaces_dir / "safe-id").is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(not HAS_JQ, reason="run_sweep.sh requires jq")
def test_duplicate_manifest_ids_fail_before_first_cleanup(tmp_path: Path) -> None:
    sweep_root = tmp_path / "sweep"
    results_dir = sweep_root / "results"
    target_dir = sweep_root / "workspaces" / "safe-id"
    target_dir.mkdir(parents=True)
    sentinel = target_dir / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {"identifier": "safe-id", "description": "first"},
                {"identifier": "safe-id", "description": "duplicate"},
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), str(manifest), str(results_dir)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "duplicate sweep identifier" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(not HAS_JQ, reason="run_sweep.sh requires jq")
def test_valid_sweep_replaces_only_the_exact_workspace_child(tmp_path: Path) -> None:
    sweep_root = tmp_path / "sweep"
    results_dir = sweep_root / "results"
    workspaces_dir = sweep_root / "workspaces"
    target_dir = workspaces_dir / "safe-id"
    sibling_dir = workspaces_dir / "keep"
    target_dir.mkdir(parents=True)
    sibling_dir.mkdir()
    (target_dir / "stale.txt").write_text("remove", encoding="utf-8")
    sibling_sentinel = sibling_dir / "sentinel.txt"
    sibling_sentinel.write_text("keep", encoding="utf-8")

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps([{"identifier": "safe-id", "description": "safe test"}]),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_autoctx = bin_dir / "autoctx"
    fake_autoctx.write_text('#!/bin/sh\nprintf \'{"status":"completed"}\\n\'\n', encoding="utf-8")
    fake_autoctx.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}

    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), str(manifest), str(results_dir), "--iterations", "1", "--timeout", "1"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not (target_dir / "stale.txt").exists()
    assert (target_dir / "runs").is_dir()
    assert sibling_sentinel.read_text(encoding="utf-8") == "keep"
    assert json.loads((results_dir / "index.json").read_text(encoding="utf-8")) == ["safe-id"]
