"""Verify the built package can install its security wheel in a clean environment."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    environment = {name: value for name, value in os.environ.items() if name != "PYTHONPATH"}
    with tempfile.TemporaryDirectory(prefix="cuda-distribution-", dir=args.work_dir) as temporary:
        working = Path(temporary).resolve()
        venv = working / "venv"
        subprocess.run(["uv", "venv", "--python", sys.executable, str(venv)], check=True, env=environment)
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        install = ["uv", "pip", "install", "--python", str(python)]
        subprocess.run(
            [*install, "-r", str(ROOT / "infra/docker/requirements.lock")], check=True, env=environment,
        )
        subprocess.run([*install, "--no-deps", str(wheel)], check=True, env=environment)
        subprocess.run(
            [str(python), "-m", "autocontext.accelerate_patch", "--install"],
            cwd=working, check=True, env=environment,
        )
        probe = (
            "import sys; from pathlib import Path; "
            "import autocontext.accelerate_patch as patch; "
            "assert Path(patch.__file__).is_relative_to(sys.prefix), patch.__file__; "
            "patch.require_patched_accelerate(); "
            "print('Built-package bootstrap and installed security hashes verified')"
        )
        subprocess.run([str(python), "-c", probe], cwd=working, check=True, env=environment)


if __name__ == "__main__":
    main()
