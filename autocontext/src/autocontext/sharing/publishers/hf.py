"""Hugging Face dataset repo publisher (AC-519).

Wraps ``huggingface-cli upload`` to publish a redacted export bundle
to a HF dataset repository. Requires ``huggingface-cli`` to be
installed and authenticated.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class HfPublishError(Exception):
    """Raised when HF publication fails."""


def publish_to_hf(
    bundle_dir: Path,
    repo_id: str,
    path_in_repo: str = "",
    repo_type: str = "dataset",
) -> str:
    """Upload bundle_dir contents to a HF dataset repo.

    Returns the repo URL on success.
    Raises HfPublishError on failure.
    """
    if not repo_id:
        raise HfPublishError("repo_id is required")

    try:
        url = _run_hf_command(
            bundle_dir=bundle_dir,
            repo_id=repo_id,
            path_in_repo=path_in_repo,
            repo_type=repo_type,
        )
    except Exception as exc:
        raise HfPublishError(f"Failed to publish to HF: {exc}") from exc

    return url.strip()


def _run_hf_command(
    bundle_dir: Path,
    repo_id: str,
    path_in_repo: str,
    repo_type: str,
) -> str:
    """Execute ``huggingface-cli upload`` and return the repo URL."""
    cmd = [
        "huggingface-cli",
        "upload",
        repo_id,
        str(bundle_dir),
    ]
    if path_in_repo:
        cmd.append(path_in_repo)
    cmd.extend(["--repo-type", repo_type])

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"huggingface-cli upload failed: {result.stderr.strip()}")

    # HF CLI outputs the URL on success
    url = result.stdout.strip()
    if not url:
        url = f"https://huggingface.co/datasets/{repo_id}"

    return url
