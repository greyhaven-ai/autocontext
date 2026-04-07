"""Environment snapshot prompt rendering (AC-503)."""

from __future__ import annotations

import json

from autocontext.bootstrap.snapshot import EnvironmentSnapshot


def render_prompt_section(snapshot: EnvironmentSnapshot) -> str:
    """Render a compact markdown section for prompt injection (~300-500 chars)."""
    lines: list[str] = ["## Environment"]

    # Core line: Python version | OS | shell | CPU | RAM | disk
    core_parts = [
        f"Python {snapshot.python_version}",
        f"{snapshot.os_name} {snapshot.os_version}",
        snapshot.shell.rsplit("/", 1)[-1] if snapshot.shell else "",
        f"{snapshot.cpu_count} CPU" if snapshot.cpu_count else "",
        f"{snapshot.memory_total_mb}MB RAM" if snapshot.memory_total_mb else "",
        f"{snapshot.disk_free_gb}GB free" if snapshot.disk_free_gb else "",
    ]
    lines.append(" | ".join(p for p in core_parts if p))

    # Git
    if snapshot.git_branch:
        dirty = ", dirty" if snapshot.git_dirty else ", clean"
        worktree = ", worktree" if snapshot.git_worktree else ""
        commit = f" ({snapshot.git_commit}{dirty}{worktree})" if snapshot.git_commit else ""
        lines.append(f"Git: {snapshot.git_branch}{commit}")

    # Runtimes
    if snapshot.available_runtimes:
        rt_parts = [f"{name} {ver}" for name, ver in sorted(snapshot.available_runtimes.items())]
        lines.append(f"Runtimes: {', '.join(rt_parts)}")

    # Filesystem summary
    if snapshot.notable_files:
        top_files = snapshot.notable_files[:8]
        extras = ""
        if snapshot.file_count or snapshot.directory_count:
            extras = f" ({snapshot.file_count} files, {snapshot.directory_count} dirs)"
        lines.append(f"Notable: {', '.join(top_files)}{extras}")

    # Packages summary
    if snapshot.installed_packages:
        pkg_count = len(snapshot.installed_packages)
        lockfile_note = f" ({', '.join(snapshot.lockfiles_found)})" if snapshot.lockfiles_found else ""
        lines.append(f"Packages: {pkg_count} top-level{lockfile_note}")

    return "\n".join(lines)


def render_full_json(snapshot: EnvironmentSnapshot) -> str:
    """Full JSON serialization for artifact persistence."""
    return json.dumps(snapshot.to_dict(), indent=2, sort_keys=True)
