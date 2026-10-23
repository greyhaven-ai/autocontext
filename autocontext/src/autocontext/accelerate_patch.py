"""Install and verify the bundled Accelerate checkpoint security build."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

BUNDLE = Path(__file__).parent / "_vendor/accelerate"


def verified_bundle(bundle: Path = BUNDLE) -> tuple[Path, dict[str, Any]]:
    manifest = json.loads((bundle / "manifest.json").read_text())
    wheel = bundle / manifest["wheel"]
    if wheel.name != manifest["wheel"] or wheel.parent != bundle:
        raise RuntimeError("Invalid bundled Accelerate wheel path")
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != manifest["wheel_sha256"]:
        raise RuntimeError("Bundled Accelerate wheel hash mismatch")
    return wheel, manifest


def require_patched_accelerate() -> None:
    """Reject stock or altered Accelerate before a CUDA model-loading entry point."""
    _, manifest = verified_bundle()
    message = (
        "CUDA requires the maintained Accelerate security build. Install it first with "
        "python -m autocontext.accelerate_patch --install, then install autocontext[cuda]."
    )
    try:
        distribution = metadata.distribution("accelerate")
    except metadata.PackageNotFoundError as exc:
        raise RuntimeError(message) from exc
    if distribution.version != manifest["version"]:
        raise RuntimeError(message)
    for relative, expected in manifest["patched_files"].items():
        path = Path(str(distribution.locate_file(relative)))
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError(message)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="Install the verified bundled wheel into this interpreter")
    args = parser.parse_args()
    wheel, manifest = verified_bundle()
    if args.install:
        if importlib.util.find_spec("pip") is not None:
            command = [sys.executable, "-m", "pip", "install", "--no-deps", "--force-reinstall", str(wheel)]
        elif (uv := shutil.which("uv")) is not None:
            command = [uv, "pip", "install", "--python", sys.executable, "--no-deps", "--reinstall", str(wheel)]
        else:
            parser.error("Install pip or uv in this environment, then rerun --install")
        subprocess.run(command, check=True)
    require_patched_accelerate()
    print(f"Verified maintained Accelerate {manifest['version']}")


if __name__ == "__main__":
    main()
