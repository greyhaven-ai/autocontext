#!/usr/bin/env python3
"""Sync release/version copy from docs/release-manifest.json."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "docs" / "release-manifest.json"
WHATS_NEW_BLOCK_START = "<!-- autocontext-whats-new:start -->"
WHATS_NEW_BLOCK_END = "<!-- autocontext-whats-new:end -->"
_VERSION_RE = r"\d+\.\d+\.\d+"


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    core_version: str
    pi_version: str
    pi_autoctx_dependency: str
    whats_new: tuple[str, ...]


def load_release_manifest(path: Path = MANIFEST_PATH) -> ReleaseManifest:
    data = json.loads(path.read_text(encoding="utf-8"))
    return ReleaseManifest(
        core_version=str(data["core_version"]),
        pi_version=str(data["pi_version"]),
        pi_autoctx_dependency=str(data["pi_autoctx_dependency"]),
        whats_new=tuple(str(item) for item in data["whats_new"]),
    )


def render_whats_new_asset(manifest: ReleaseManifest) -> str:
    return "\n".join(manifest.whats_new) + "\n"


def render_whats_new_heading(manifest: ReleaseManifest) -> str:
    return f"What's New in {manifest.core_version}"


def render_readme_whats_new_block(manifest: ReleaseManifest) -> str:
    items = "\n".join(f"- {item}" for item in manifest.whats_new)
    return f"{WHATS_NEW_BLOCK_START}\n## {render_whats_new_heading(manifest)}\n\n{items}\n{WHATS_NEW_BLOCK_END}"


def render_pi_release_note(
    manifest: ReleaseManifest, prefix: str = "Pi is on a separate package line"
) -> str:
    return (
        f"{prefix}: `pi-autocontext@{manifest.pi_version}` depends on "
        f"`autoctx@{manifest.pi_autoctx_dependency}`. A follow-up Pi release can move it to a newer "
        "`autoctx` line after the core npm package is live."
    )


def _replace_block(text: str, start: str, end: str, replacement: str) -> str:
    pattern = re.compile(rf"{re.escape(start)}.*?{re.escape(end)}", re.DOTALL)
    if not pattern.search(text):
        raise ValueError(f"sync markers not found for {start}")
    return pattern.sub(replacement, text, count=1)


def _replace_versions(text: str, manifest: ReleaseManifest) -> str:
    text = re.sub(
        rf"autocontext=={_VERSION_RE}", f"autocontext=={manifest.core_version}", text
    )
    text = re.sub(rf"autoctx@{_VERSION_RE}", f"autoctx@{manifest.core_version}", text)
    return re.sub(
        rf"pi-autocontext@{_VERSION_RE}", f"pi-autocontext@{manifest.pi_version}", text
    )


def _replace_root_pi_notes(text: str, manifest: ReleaseManifest) -> str:
    separate_note = re.compile(
        r"Pi is on a separate package line: `pi-autocontext@[^`]+` depends on `autoctx@[^`]+`\. "
        r"A follow-up Pi release can move it to a newer `autoctx` line after the core npm package is live\."
    )
    text = separate_note.sub(render_pi_release_note(manifest), text)
    blockquote_note = re.compile(
        r"`pi-autocontext@[^`]+` still depends on `autoctx@[^`]+`; "
        r"a follow-up Pi release can move it to a newer `autoctx` line after the core npm package is live\."
    )
    return blockquote_note.sub(
        f"`pi-autocontext@{manifest.pi_version}` still depends on `autoctx@{manifest.pi_autoctx_dependency}`; "
        "a follow-up Pi release can move it to a newer `autoctx` line after the core npm package is live.",
        text,
    )


def sync_root_readme(text: str, manifest: ReleaseManifest) -> str:
    text = _replace_versions(text, manifest)
    text = _replace_root_pi_notes(text, manifest)
    return _replace_block(
        text,
        WHATS_NEW_BLOCK_START,
        WHATS_NEW_BLOCK_END,
        render_readme_whats_new_block(manifest),
    )


def sync_python_readme(text: str, manifest: ReleaseManifest) -> str:
    return re.sub(
        rf"autocontext=={_VERSION_RE}", f"autocontext=={manifest.core_version}", text
    )


def sync_ts_readme(text: str, manifest: ReleaseManifest) -> str:
    return re.sub(rf"autoctx@{_VERSION_RE}", f"autoctx@{manifest.core_version}", text)


def render_pi_package_note(manifest: ReleaseManifest) -> str:
    return (
        f"Current package note: `pi-autocontext@{manifest.pi_version}` is on a separate Pi extension line "
        f"and depends on `autoctx@{manifest.pi_autoctx_dependency}`. A follow-up Pi release can move it "
        "to a newer `autoctx` line after the core npm package is live."
    )


def sync_pi_readme(text: str, manifest: ReleaseManifest) -> str:
    note = re.compile(
        r"Current package note: `pi-autocontext@[^`]+` is on a separate Pi extension line and "
        r"depends on `autoctx@[^`]+`\. A follow-up Pi release can move it to a newer `autoctx` line "
        r"after the core npm package is live\."
    )
    return note.sub(render_pi_package_note(manifest), _replace_versions(text, manifest))


def sync_banner_py(text: str, manifest: ReleaseManifest) -> str:
    return re.sub(
        r"README_WHATS_NEW_HEADING = \"[^\"]+\"",
        f'README_WHATS_NEW_HEADING = "{render_whats_new_heading(manifest)}"',
        text,
    )


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return cast(dict[str, Any], data)


def _read_toml(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], tomllib.loads(path.read_text(encoding="utf-8")))


def _read_python_init_version(path: Path) -> str:
    match = re.search(r'^__version__ = "([^"]+)"$', path.read_text(encoding="utf-8"), re.MULTILINE)
    if match is None:
        raise ValueError(f"{path} does not define __version__")
    return match.group(1)


def planned_release_surface_updates(manifest: ReleaseManifest) -> dict[Path, str]:
    return {
        REPO_ROOT / "README.md": sync_root_readme(
            (REPO_ROOT / "README.md").read_text(encoding="utf-8"), manifest
        ),
        REPO_ROOT / "autocontext" / "README.md": sync_python_readme(
            (REPO_ROOT / "autocontext" / "README.md").read_text(encoding="utf-8"),
            manifest,
        ),
        REPO_ROOT / "ts" / "README.md": sync_ts_readme(
            (REPO_ROOT / "ts" / "README.md").read_text(encoding="utf-8"), manifest
        ),
        REPO_ROOT / "pi" / "README.md": sync_pi_readme(
            (REPO_ROOT / "pi" / "README.md").read_text(encoding="utf-8"), manifest
        ),
        REPO_ROOT / "autocontext" / "assets" / "whats_new.txt": render_whats_new_asset(
            manifest
        ),
        REPO_ROOT / "autocontext" / "src" / "autocontext" / "banner.py": sync_banner_py(
            (REPO_ROOT / "autocontext" / "src" / "autocontext" / "banner.py").read_text(
                encoding="utf-8"
            ),
            manifest,
        ),
    }


def check_release_surfaces(manifest: ReleaseManifest) -> list[str]:
    issues: list[str] = []
    pyproject = _read_toml(REPO_ROOT / "autocontext" / "pyproject.toml")
    pyproject_version = str(cast(dict[str, Any], pyproject["project"])["version"])
    python_init_version = _read_python_init_version(
        REPO_ROOT / "autocontext" / "src" / "autocontext" / "__init__.py"
    )
    package_version = str(_read_json(REPO_ROOT / "ts" / "package.json")["version"])
    pi_package = _read_json(REPO_ROOT / "pi" / "package.json")
    pi_version = str(pi_package["version"])
    pi_dependencies = cast(dict[str, Any], pi_package["dependencies"])
    pi_autoctx_dependency = str(pi_dependencies["autoctx"])
    if pyproject_version != manifest.core_version:
        issues.append(
            f"autocontext/pyproject.toml version {pyproject_version} != manifest {manifest.core_version}"
        )
    if python_init_version != manifest.core_version:
        issues.append(
            f"autocontext/src/autocontext/__init__.py version {python_init_version} != manifest {manifest.core_version}"
        )
    if package_version != manifest.core_version:
        issues.append(
            f"ts/package.json version {package_version} != manifest {manifest.core_version}"
        )
    if pi_version != manifest.pi_version:
        issues.append(f"pi/package.json version {pi_version} != manifest {manifest.pi_version}")
    if pi_autoctx_dependency != manifest.pi_autoctx_dependency:
        issues.append(
            f"pi/package.json autoctx dependency {pi_autoctx_dependency} != manifest {manifest.pi_autoctx_dependency}"
        )
    for path, expected in planned_release_surface_updates(manifest).items():
        if path.read_text(encoding="utf-8") != expected:
            issues.append(
                f"{path.relative_to(REPO_ROOT)} is not synced with docs/release-manifest.json"
            )
    return issues


def write_release_surfaces(manifest: ReleaseManifest) -> None:
    for path, expected in planned_release_surface_updates(manifest).items():
        if path.read_text(encoding="utf-8") != expected:
            path.write_text(expected, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="fail if public release surfaces are stale"
    )
    args = parser.parse_args()
    manifest = load_release_manifest()
    if args.check:
        issues = check_release_surfaces(manifest)
        for issue in issues:
            print(issue)
        return 1 if issues else 0
    write_release_surfaces(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
