"""Build hook that bundles ``docs/cli-contract.json`` into the wheel.

The contract lives at the repo root (``docs/cli-contract.json``) so the
TypeScript and Python sides share a single source of truth. To ship it
inside the wheel, slice 5 of AC-697 originally used a static
``force-include`` rule pointing at ``../docs/cli-contract.json``.

That works for ``uv build`` invoked directly from the source tree, but
the publish-python workflow runs ``uv build`` which builds the wheel
from the freshly produced sdist. Inside the extracted sdist there is no
parent ``docs/`` directory, so the static rule resolves to a missing
file and the build fails.

This hook resolves the contract from whichever location actually exists
(repo tree during dev builds, in-sdist copy during release builds),
stages it under ``build/`` next to the hook, and registers an absolute
``force_include`` entry for the wheel.

The sibling ``[tool.hatch.build.targets.sdist.force-include]`` rule in
``pyproject.toml`` is what places the file at ``docs/cli-contract.json``
inside the sdist so the wheel-from-sdist branch finds it here.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CliContractBuildHook(BuildHookInterface):
    PLUGIN_NAME = "cli-contract"

    def initialize(self, version: str, build_data: dict) -> None:
        if self.target_name != "wheel":
            return
        root = Path(self.root)
        candidates = [
            root.parent / "docs" / "cli-contract.json",
            root / "docs" / "cli-contract.json",
        ]
        source = next((p for p in candidates if p.is_file()), None)
        if source is None:
            tried = ", ".join(str(c) for c in candidates)
            raise FileNotFoundError(f"cli-contract.json not found; looked in: {tried}")
        staged = root / "build" / "_cli_contract" / "cli_contract.json"
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, staged)
        force_include = build_data.setdefault("force_include", {})
        force_include[str(staged)] = "autocontext/cli_contract.json"
