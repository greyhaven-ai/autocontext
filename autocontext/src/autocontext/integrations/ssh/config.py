"""SSH host configuration models for trusted remote execution."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SSHHostCapabilities(BaseModel):
    """Hardware and software capabilities of a trusted host."""

    cpu_cores: int = Field(default=0, ge=0)
    memory_gb: float = Field(default=0.0, ge=0.0)
    gpu_count: int = Field(default=0, ge=0)
    gpu_model: str = Field(default="")
    installed_runtimes: list[str] = Field(default_factory=list)


class SSHHostConfig(BaseModel):
    """Configuration for a single trusted SSH host."""

    name: str = Field(description="Human-readable host name")
    hostname: str = Field(description="SSH hostname or IP address")
    port: int = Field(default=22, ge=1, le=65535)
    user: str = Field(default="", description="SSH user (empty = current user)")
    identity_file: str = Field(default="", description="Path to SSH private key")
    working_directory: str = Field(default="/tmp/autocontext", description="Remote working directory")
    environment: dict[str, str] = Field(default_factory=dict, description="Environment variables to set on remote")
    capabilities: SSHHostCapabilities = Field(default_factory=SSHHostCapabilities)
    connect_timeout: int = Field(default=10, ge=1, description="SSH connection timeout in seconds")
    command_timeout: float = Field(default=120.0, ge=1.0, description="Default command execution timeout")
