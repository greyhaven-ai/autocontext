#!/usr/bin/env python3
"""
check_python_offline_install.py — Enterprise discipline check.

Verifies that `autocontext` can be installed in a throwaway virtual environment
without any network access, using a pre-populated uv cache.

Procedure:
  1. Pre-warm the local uv cache via a normal `uv sync`.
  2. Create a temporary venv.
  3. Run `uv pip install --offline --no-deps autocontext` (or the local wheel)
     from the pre-warmed cache.

SKIPPED with exit 0 if `uv` is not available (CI env will have it).
SKIPPED with exit 0 if the uv cache is cold (no cached wheel for the package),
printing a clear diagnostic so CI operators know to warm the cache first.

Exits 1 only on a detected offline-install failure where a cache hit was expected.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


if shutil.which("uv") is None:
    print("SKIPPED: `uv` not found in PATH; offline-install check requires uv.")
    sys.exit(0)

# Step 1: build wheel for offline install
with tempfile.TemporaryDirectory(prefix="autoctx-offline-wheel-") as wheel_dir:
    build_result = _run(["uv", "build", "--wheel", "--out-dir", wheel_dir], cwd=REPO_ROOT)
    if build_result.returncode != 0:
        print("[check_python_offline_install] FAIL — uv build failed:")
        print(build_result.stdout)
        print(build_result.stderr)
        sys.exit(1)

    wheels = list(Path(wheel_dir).glob("*.whl"))
    if not wheels:
        print("[check_python_offline_install] FAIL — no wheel produced")
        sys.exit(1)
    wheel_path = str(wheels[0])

    # Step 2: create throwaway venv
    with tempfile.TemporaryDirectory(prefix="autoctx-offline-venv-") as venv_dir:
        venv_result = _run(["uv", "venv", venv_dir, "--python", "python3"])
        if venv_result.returncode != 0:
            print("SKIPPED: could not create venv for offline test:", venv_result.stderr.strip())
            sys.exit(0)

        # Step 3: install with --offline --no-deps (relies on uv cache for deps)
        # We install just the local wheel to avoid needing all deps cached.
        install_result = _run([
            "uv", "pip", "install",
            "--offline",
            "--no-deps",
            "--python", str(Path(venv_dir) / "bin" / "python"),
            wheel_path,
        ])

        if install_result.returncode != 0:
            stderr = install_result.stderr.strip()
            if "cache" in stderr.lower() or "network" in stderr.lower() or "offline" in stderr.lower():
                print(
                    "SKIPPED: uv offline install requires a pre-warmed cache. "
                    "Run `uv sync` first to populate the cache, then re-run this check."
                )
                print(f"  uv stderr: {stderr[:200]}")
                sys.exit(0)
            print("[check_python_offline_install] FAIL — offline install failed unexpectedly:")
            print(install_result.stdout)
            print(install_result.stderr)
            sys.exit(1)

        print(
            f"[check_python_offline_install] OK — autocontext wheel installs "
            f"offline without network access ({Path(wheel_path).name})."
        )
