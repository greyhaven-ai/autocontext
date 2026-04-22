#!/usr/bin/env python3
"""
check_python_reproducible_wheel.py — Enterprise discipline check.

Builds the wheel twice with `uv build --wheel` and compares the SHA-256
hashes of the resulting .whl files to assert byte-identical output
(reproducible build).

Exits 0 on success (hashes match or tool unavailable).
Exits 1 if hashes differ — non-reproducible build detected.

SKIPPED with exit 0 if `uv` is not available in PATH (CI env will have it).
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_wheel(dest: Path) -> Path:
    """Run `uv build --wheel --out-dir <dest>` and return the .whl path."""
    result = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dest)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("[check_python_reproducible_wheel] FAIL — uv build failed:")
        print(result.stdout)
        print(result.stderr)
        sys.exit(1)
    wheels = list(dest.glob("*.whl"))
    if not wheels:
        print("[check_python_reproducible_wheel] FAIL — no .whl produced in", dest)
        sys.exit(1)
    return wheels[0]


if shutil.which("uv") is None:
    print("SKIPPED: `uv` not found in PATH; reproducible-wheel check requires uv.")
    sys.exit(0)

with tempfile.TemporaryDirectory(prefix="autoctx-wheel-a-") as dir_a, \
        tempfile.TemporaryDirectory(prefix="autoctx-wheel-b-") as dir_b:
    whl_a = _build_wheel(Path(dir_a))
    whl_b = _build_wheel(Path(dir_b))

    sha_a = _sha256(whl_a)
    sha_b = _sha256(whl_b)

    if sha_a != sha_b:
        print("[check_python_reproducible_wheel] FAIL — wheels are NOT byte-identical:")
        print(f"  build 1: {whl_a.name}  sha256={sha_a}")
        print(f"  build 2: {whl_b.name}  sha256={sha_b}")
        print(
            "  Non-reproducible builds may contain timestamps or non-deterministic ordering. "
            "Set SOURCE_DATE_EPOCH=0 or check for embedded timestamps."
        )
        sys.exit(1)

    print(
        f"[check_python_reproducible_wheel] OK — two builds are byte-identical "
        f"(sha256={sha_a[:16]}…)."
    )
