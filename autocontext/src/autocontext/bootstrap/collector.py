"""Environment snapshot collector (AC-503).

Gathers environment info via stdlib only. Each helper catches all exceptions
and returns sensible defaults — the collector never raises.
"""

from __future__ import annotations

import datetime
import importlib.metadata
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from autocontext.bootstrap.snapshot import EnvironmentSnapshot, PackageInfo

_SUBPROCESS_TIMEOUT = 0.5  # seconds
_MAX_NOTABLE_FILES = 50
_KNOWN_LOCKFILES = frozenset(
    {
        "poetry.lock",
        "Pipfile.lock",
        "uv.lock",
        "pdm.lock",
        "conda-lock.yml",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lock",
        "Gemfile.lock",
        "Cargo.lock",
        "go.sum",
        "composer.lock",
    }
)
_RUNTIME_CHECKS = {
    "node": ["node", "--version"],
    "go": ["go", "version"],
    "ruby": ["ruby", "--version"],
    "java": ["java", "-version"],
    "rustc": ["rustc", "--version"],
    "cargo": ["cargo", "--version"],
    "deno": ["deno", "--version"],
    "bun": ["bun", "--version"],
}


def collect_snapshot() -> EnvironmentSnapshot:
    """Collect full environment snapshot. Never raises."""
    core = _collect_core()
    runtimes = _collect_runtimes()
    packages = _collect_packages()
    fs = _collect_filesystem(core["working_directory"])
    git = _collect_git()
    system = _collect_system()

    return EnvironmentSnapshot(
        **core,
        **runtimes,
        **packages,
        **fs,
        **git,
        **system,
        collected_at=datetime.datetime.now(datetime.UTC).isoformat(),
    )


def _collect_core() -> dict[str, Any]:
    try:
        cwd = os.getcwd()
    except OSError:
        cwd = ""
    return {
        "working_directory": cwd,
        "os_name": platform.system(),
        "os_version": platform.release(),
        "shell": os.environ.get("SHELL", os.environ.get("COMSPEC", "")),
        "hostname": platform.node(),
        "username": _get_username(),
    }


def _get_username() -> str:
    try:
        return os.getlogin()
    except OSError:
        return os.environ.get("USER", os.environ.get("USERNAME", ""))


def _collect_runtimes() -> dict[str, Any]:
    available: dict[str, str] = {}
    for name, cmd in _RUNTIME_CHECKS.items():
        if shutil.which(cmd[0]):
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT)  # noqa: S603
                version = result.stdout.strip() or result.stderr.strip()
                # Extract just the version number from verbose output
                for token in version.split():
                    if token and token[0].isdigit():
                        available[name] = token.rstrip(",")
                        break
                else:
                    available[name] = version[:50]
            except (subprocess.TimeoutExpired, OSError):
                available[name] = "found"
    return {
        "python_version": platform.python_version(),
        "available_runtimes": available,
    }


def _collect_packages() -> dict[str, Any]:
    packages: list[PackageInfo] = []
    try:
        for dist in importlib.metadata.distributions():
            metadata = dist.metadata
            name = metadata["Name"] if "Name" in metadata else ""
            version = metadata["Version"] if "Version" in metadata else ""
            if name:
                packages.append(PackageInfo(name=name, version=version))
    except Exception:
        pass
    # Deduplicate and sort
    seen: set[str] = set()
    unique: list[PackageInfo] = []
    for p in sorted(packages, key=lambda x: x.name.lower()):
        key = p.name.lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)

    lockfiles: list[str] = []
    try:
        cwd = Path.cwd()
        for name in sorted(_KNOWN_LOCKFILES):
            if (cwd / name).exists():
                lockfiles.append(name)
    except OSError:
        pass

    return {"installed_packages": unique, "lockfiles_found": lockfiles}


def _collect_filesystem(cwd: str) -> dict[str, Any]:
    notable: list[str] = []
    dir_count = 0
    file_count = 0
    try:
        root = Path(cwd)
        for entry in sorted(root.iterdir()):
            if entry.name.startswith(".") and entry.name not in {".env.example", ".gitignore", ".dockerignore"}:
                continue
            if entry.is_dir():
                dir_count += 1
            else:
                file_count += 1
            if len(notable) < _MAX_NOTABLE_FILES:
                suffix = "/" if entry.is_dir() else ""
                notable.append(f"{entry.name}{suffix}")
    except OSError:
        pass
    return {
        "notable_files": notable,
        "directory_count": dir_count,
        "file_count": file_count,
    }


def _collect_git() -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "git_branch": None,
        "git_commit": None,
        "git_dirty": False,
        "git_worktree": False,
    }
    if not shutil.which("git"):
        return defaults
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],  # noqa: S603, S607
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
        if branch.returncode != 0:
            return defaults
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S603, S607
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"],  # noqa: S603, S607
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
        worktree_check = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],  # noqa: S603, S607
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
        git_dir = subprocess.run(
            ["git", "rev-parse", "--git-dir"],  # noqa: S603, S607
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
        is_worktree = (
            worktree_check.returncode == 0 and git_dir.returncode == 0 and worktree_check.stdout.strip() != git_dir.stdout.strip()
        )
        return {
            "git_branch": branch.stdout.strip() or None,
            "git_commit": commit.stdout.strip() or None,
            "git_dirty": bool(status.stdout.strip()),
            "git_worktree": is_worktree,
        }
    except (subprocess.TimeoutExpired, OSError):
        return defaults


def _collect_system() -> dict[str, Any]:
    cpu_count = os.cpu_count() or 0
    mem_total = 0
    mem_available = 0
    disk_free = 0.0

    # Memory: try /proc/meminfo (Linux), then sysctl (macOS), then fallback
    try:
        meminfo = Path("/proc/meminfo")
        if meminfo.exists():
            text = meminfo.read_text()
            for line in text.splitlines():
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1]) // 1024  # kB → MB
                elif line.startswith("MemAvailable:"):
                    mem_available = int(line.split()[1]) // 1024
        elif sys.platform == "darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],  # noqa: S603, S607
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT,
            )
            if result.returncode == 0:
                mem_total = int(result.stdout.strip()) // (1024 * 1024)
            # Available memory on macOS: approximate via vm_stat
            vm = subprocess.run(
                ["vm_stat"],  # noqa: S603, S607
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT,
            )
            if vm.returncode == 0:
                free_pages = 0
                for line in vm.stdout.splitlines():
                    if "Pages free:" in line or "Pages inactive:" in line:
                        parts = line.split(":")
                        if len(parts) == 2:
                            free_pages += int(parts[1].strip().rstrip("."))
                mem_available = (free_pages * 4096) // (1024 * 1024)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass

    # Disk
    try:
        usage = shutil.disk_usage(os.getcwd())
        disk_free = round(usage.free / (1024**3), 1)
    except OSError:
        pass

    return {
        "memory_total_mb": mem_total,
        "memory_available_mb": mem_available,
        "disk_free_gb": disk_free,
        "cpu_count": cpu_count,
    }
