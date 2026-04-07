"""GitHub Gist publisher (AC-519).

Wraps ``gh gist create`` to publish a redacted export bundle as a public
or secret Gist. Requires the ``gh`` CLI to be installed and authenticated.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class GistPublishError(Exception):
    """Raised when Gist publication fails."""


def publish_to_gist(
    bundle_dir: Path,
    description: str = "Autocontext session export",
    public: bool = False,
) -> str:
    """Publish all files in bundle_dir as a GitHub Gist.

    Returns the Gist URL on success.
    Raises GistPublishError on failure.
    """
    files = sorted(p for p in bundle_dir.iterdir() if p.is_file())
    if not files:
        raise GistPublishError("No files to publish in bundle directory")

    file_args: list[str] = []
    for f in files:
        file_args.append(str(f))

    try:
        url = _run_gh_command(file_args, description=description, public=public)
    except Exception as exc:
        raise GistPublishError(f"Failed to publish Gist: {exc}") from exc

    return url.strip()


def _run_gh_command(
    files: list[str],
    description: str,
    public: bool,
) -> str:
    """Execute ``gh gist create`` and return the Gist URL."""
    cmd = ["gh", "gist", "create"]
    if public:
        cmd.append("--public")
    cmd.extend(["--desc", description])
    cmd.extend(files)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh gist create failed: {result.stderr.strip()}")

    return result.stdout.strip()
