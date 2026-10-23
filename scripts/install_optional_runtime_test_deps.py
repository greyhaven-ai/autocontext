"""Install the locked maintained build with CPU Torch for the security CI job."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import tomllib
from importlib import metadata
from pathlib import Path

from packaging.markers import Marker
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    lock = tomllib.loads((ROOT / "autocontext/uv.lock").read_text())
    versions = {}
    for package in lock["package"]:
        markers = package.get("resolution-markers", [])
        if markers and not any(Marker(marker).evaluate() for marker in markers):
            continue
        name, version = package["name"], package["version"]
        if name in versions and versions[name] != version:
            raise ValueError(f"Ambiguous locked version for {name}")
        versions[name] = version
    command = ["uv", "pip", "install", "--python", sys.executable]
    subprocess.run([*command, "--torch-backend", "cpu", "--no-deps", f"torch=={versions['torch']}"], check=True)
    bundle = ROOT / "autocontext/src/autocontext/_vendor/accelerate"
    wheel, = bundle.glob("*.whl")
    subprocess.run([*command, "--no-deps", str(wheel)], check=True)
    requirements = []
    for package in ("torch", "accelerate"):
        for raw in metadata.requires(package) or []:
            requirement = Requirement(raw)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                requirements.append(str(requirement))
    with tempfile.TemporaryDirectory(prefix="cuda-test-constraints-") as temporary:
        constraints = Path(temporary) / "constraints.txt"
        constraints.write_text("".join(f"{name}=={version}\n" for name, version in sorted(versions.items())))
        subprocess.run([*command, "--constraint", str(constraints), *requirements], check=True)
    subprocess.run(["uv", "pip", "check", "--python", sys.executable], check=True)


if __name__ == "__main__":
    main()
