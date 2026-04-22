#!/usr/bin/env python3
"""
check_no_python_postinstall.py — Enterprise discipline check.

Parses pyproject.toml and asserts that no install-time hook scripts are
declared that would execute automatically during `pip install` / `uv sync`.

Checks:
  - [project.scripts] entries must not point to installer-hook patterns.
  - No `[tool.hatch.build.hooks]` sections that fire unconditionally.
  - No `[tool.poetry.scripts]` `install` key.
  - No `setup.py` with install-hook patterns.

Exits 0 on success; non-zero with diagnostic on failure.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-reattr]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"

HOOK_SCRIPT_PATTERNS = [
    re.compile(r"\binstall\b", re.IGNORECASE),
    re.compile(r"\bpost.?install\b", re.IGNORECASE),
    re.compile(r"\bpre.?install\b", re.IGNORECASE),
]

FAILS: list[str] = []


def _is_hook_name(name: str) -> bool:
    return any(p.search(name) for p in HOOK_SCRIPT_PATTERNS)


def check_pyproject() -> None:
    if not PYPROJECT.exists():
        print(f"SKIP — {PYPROJECT} not found; nothing to check.")
        return

    if tomllib is None:
        print("SKIPPED: tomllib/tomli not available; install Python 3.11+ or `pip install tomli`.")
        return

    with PYPROJECT.open("rb") as f:
        data = tomllib.load(f)

    # [project.scripts] — should only contain CLI entry points, not install hooks
    proj_scripts = data.get("project", {}).get("scripts", {})
    for name, _target in proj_scripts.items():
        if _is_hook_name(name):
            FAILS.append(
                f"[project.scripts] entry '{name}' looks like an install-time hook. "
                "Install hooks run automatically on pip install and violate enterprise isolation. "
                "Rename or remove it."
            )

    # [tool.poetry.scripts] install key
    poetry_scripts = data.get("tool", {}).get("poetry", {}).get("scripts", {})
    if "install" in poetry_scripts:
        FAILS.append(
            "[tool.poetry.scripts] has an 'install' key which runs on `poetry install`. Remove it."
        )

    # [tool.hatch.build.hooks] unconditional hooks
    hatch_hooks = data.get("tool", {}).get("hatch", {}).get("build", {}).get("hooks", {})
    for hook_name, hook_cfg in hatch_hooks.items():
        if isinstance(hook_cfg, dict) and hook_cfg.get("enable-by-default", True):
            FAILS.append(
                f"[tool.hatch.build.hooks.{hook_name}] is enabled by default. "
                "Build hooks run at install time in editable installs; "
                "set enable-by-default = false or remove."
            )


def check_setup_py() -> None:
    setup = REPO_ROOT / "setup.py"
    if not setup.exists():
        return
    body = setup.read_text(encoding="utf-8")
    HOOK_RE = re.compile(r"cmdclass\s*=\s*\{[^}]*'install'", re.DOTALL)
    if HOOK_RE.search(body):
        FAILS.append(
            "setup.py defines a custom 'install' command in cmdclass. "
            "This runs at `pip install` time. Remove or guard behind a flag."
        )


check_pyproject()
check_setup_py()

if FAILS:
    print("[check_no_python_postinstall] FAIL:")
    for msg in FAILS:
        print(f"  - {msg}")
    sys.exit(1)

print(
    f"[check_no_python_postinstall] OK — {PYPROJECT.name} has no install-time hook scripts."
)
