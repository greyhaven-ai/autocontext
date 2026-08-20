"""Persistent interpreter workspace fields (AC-901).

Extracted from AppSettings to keep config/settings.py under the module
size limit (AC-905 pattern). The workspace is opt-in and off by default:
the serialize-everything path remains the baseline until benchmarked per
scenario. Lifecycle isolation only, not a security sandbox; see
autocontext.execution.interpreter_workspace.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from autocontext.runtime_images import PINNED_PYTHON_RUNTIME_IMAGE, require_pinned_runtime_image


class WorkspaceInterpreterFields(BaseModel):
    """Mixin holding the persistent interpreter workspace settings."""

    workspace_interpreter_enabled: bool = Field(
        default=False,
        description="Opt-in persistent interpreter workspace for multi-generation runs",
    )
    workspace_interpreter_timeout_seconds: float = Field(default=10.0, gt=0, allow_inf_nan=False)
    workspace_interpreter_backend: Literal["interpreter", "docker"] = Field(
        default="interpreter",
        description="Execution backend selected when the workspace is enabled",
    )
    workspace_interpreter_execute_candidates: bool = Field(
        default=False,
        description="Execute multi-generation candidates before judging their observable result",
    )
    workspace_interpreter_capabilities_approved: bool = Field(
        default=False,
        description="Explicit operator approval for the configured isolated workspace capabilities",
    )
    workspace_interpreter_allowed_imports: tuple[str, ...] = ()
    workspace_interpreter_allowed_commands: tuple[str, ...] = ()
    workspace_interpreter_docker_image: str = PINNED_PYTHON_RUNTIME_IMAGE
    workspace_interpreter_memory_mb: int = Field(default=512, ge=64)
    workspace_interpreter_cpu_count: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    workspace_interpreter_pids_limit: int = Field(default=64, ge=2)
    workspace_interpreter_max_file_bytes: int = Field(default=8 * 1024 * 1024, ge=1)
    workspace_interpreter_max_workspace_bytes: int = Field(default=64 * 1024 * 1024, ge=1)
    workspace_interpreter_max_workspace_inodes: int = Field(default=4096, ge=1)

    @field_validator(
        "workspace_interpreter_allowed_imports",
        "workspace_interpreter_allowed_commands",
        mode="before",
    )
    @classmethod
    def _parse_json_allowlist(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("workspace allowlists must be JSON arrays") from exc
        if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
            raise ValueError("workspace allowlists must contain only strings")
        return tuple(parsed)

    @field_validator("workspace_interpreter_docker_image")
    @classmethod
    def _workspace_image_is_immutable(cls, value: str) -> str:
        return require_pinned_runtime_image(value)
