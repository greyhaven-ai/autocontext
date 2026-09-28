#!/usr/bin/env python3
"""Fail when ts/bun.lock and ts/package-lock.json lock different package versions.

CI installs TypeScript dependencies with ``npm ci`` from package-lock.json. bun.lock
is read only by ``bun audit`` and by developers who install with bun, and Dependabot
relocks package-lock.json alone, so nothing else notices when bun.lock drifts.

Every package name must resolve to the same set of versions in both lockfiles. npm
and bun hoist differently, so install paths and duplicate copies are not compared.
Registry dependencies only: git, file and workspace dependencies have no comparable
version and fail the check.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "ts"
# bun writes bun.lock as JSON with trailing commas, and without comments.
TRAILING_COMMA = re.compile(r",(\s*[}\]])")
# The name may itself start with "@" (a scope); the version follows the last "@".
SPEC = re.compile(r"(?P<name>.+)@(?P<version>[^@]+)")
VersionMap = dict[str, set[str]]


@dataclass(frozen=True)
class Drift:
    name: str
    npm_versions: tuple[str, ...]
    bun_versions: tuple[str, ...]


def load_bun_lock(text: str) -> Any:
    return json.loads(TRAILING_COMMA.sub(r"\1", text))


def _packages(lock: Any, lockfile: str) -> Mapping[str, Any]:
    packages = lock.get("packages") if isinstance(lock, dict) else None
    if not isinstance(packages, dict):
        raise ValueError(f"{lockfile} has no packages table")
    return packages


def npm_versions(lock: Any) -> VersionMap:
    versions: VersionMap = {}
    for path, entry in _packages(lock, "package-lock.json").items():
        if path == "":
            continue  # the project itself
        version = entry.get("version") if isinstance(entry, dict) else None
        if not isinstance(version, str) or not version:
            raise ValueError(f"package-lock.json: {path} has no version")
        # An alias such as "string-width-cjs": "npm:string-width@4.2.3" installs
        # under the alias path and records the real package in "name".
        name = entry.get("name") or path.rpartition("node_modules/")[2]
        versions.setdefault(name, set()).add(version)
    return versions


def bun_versions(lock: Any) -> VersionMap:
    versions: VersionMap = {}
    for key, entry in _packages(lock, "bun.lock").items():
        # Keys are bun's own tree paths; each entry starts with its "name@version".
        spec = entry[0] if isinstance(entry, list) and entry else None
        match = SPEC.fullmatch(spec) if isinstance(spec, str) else None
        if match is None:
            raise ValueError(f"bun.lock: {key} does not start with a name@version spec")
        versions.setdefault(match["name"], set()).add(match["version"])
    return versions


def compare_versions(npm: VersionMap, bun: VersionMap) -> list[Drift]:
    return [
        Drift(name, tuple(sorted(npm.get(name, ()))), tuple(sorted(bun.get(name, ()))))
        for name in sorted(npm.keys() | bun.keys())
        if npm.get(name) != bun.get(name)
    ]


def _listed(versions: tuple[str, ...]) -> str:
    return ", ".join(versions) if versions else "absent"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package-dir",
        type=Path,
        default=PACKAGE_DIR,
        help="directory holding package-lock.json and bun.lock (default: ts)",
    )
    args = parser.parse_args(argv)

    try:
        npm_text = (args.package_dir / "package-lock.json").read_text(encoding="utf-8")
        bun_text = (args.package_dir / "bun.lock").read_text(encoding="utf-8")
        npm = npm_versions(json.loads(npm_text))
        bun = bun_versions(load_bun_lock(bun_text))
    except (OSError, ValueError) as exc:
        print(f"bun.lock sync check error: {exc}", file=sys.stderr)
        return 2

    drift = compare_versions(npm, bun)
    if drift:
        print(
            "bun.lock and package-lock.json lock different versions:", file=sys.stderr
        )
        for item in drift:
            print(
                f"- {item.name}: package-lock.json {_listed(item.npm_versions)}; bun.lock {_listed(item.bun_versions)}",
                file=sys.stderr,
            )
        try:
            shown = args.package_dir.resolve().relative_to(REPO_ROOT)
        except ValueError:
            shown = args.package_dir
        print(
            "Regenerate bun.lock from package-lock.json with the bun version CI pins, from the repository root:\n"
            f"  cd {shown} && rm bun.lock && bun install --lockfile-only\n"
            "With no bun lockfile present, bun migrates package-lock.json and keeps its versions. "
            "A plain bun install or bun update resolves versions itself and can drift again.",
            file=sys.stderr,
        )
        return 1

    locked = sum(len(versions) for versions in npm.values())
    print(
        f"bun.lock sync: ok ({locked} versions of {len(npm)} packages match package-lock.json)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
