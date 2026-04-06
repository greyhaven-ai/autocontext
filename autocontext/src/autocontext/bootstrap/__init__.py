"""Environment snapshot bootstrapping (AC-503)."""

from __future__ import annotations

from autocontext.bootstrap.collector import collect_snapshot
from autocontext.bootstrap.redactor import RedactionConfig, redact_snapshot
from autocontext.bootstrap.renderer import render_full_json, render_prompt_section
from autocontext.bootstrap.snapshot import EnvironmentSnapshot, PackageInfo

__all__ = [
    "EnvironmentSnapshot",
    "PackageInfo",
    "collect_snapshot",
    "RedactionConfig",
    "redact_snapshot",
    "render_prompt_section",
    "render_full_json",
]
