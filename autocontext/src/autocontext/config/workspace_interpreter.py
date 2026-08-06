"""Persistent interpreter workspace fields (AC-901).

Extracted from AppSettings to keep config/settings.py under the module
size limit (AC-905 pattern). The workspace is opt-in and off by default:
the serialize-everything path remains the baseline until benchmarked per
scenario. Lifecycle isolation only, not a security sandbox; see
autocontext.execution.interpreter_workspace.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class WorkspaceInterpreterFields(BaseModel):
    """Mixin holding the persistent interpreter workspace settings."""

    workspace_interpreter_enabled: bool = Field(
        default=False,
        description="Opt-in persistent interpreter workspace for multi-generation runs",
    )
    workspace_interpreter_timeout_seconds: float = Field(default=10.0, gt=0)
