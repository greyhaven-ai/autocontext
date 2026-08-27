"""Security-sensitive compatibility settings."""

from pydantic import BaseModel, Field  # type: ignore[import-not-found]


class SecurityFields(BaseModel):
    """Settings that deliberately gate unsafe legacy execution paths."""

    unsafe_openclaw_executable_artifacts_enabled: bool = Field(
        default=False,
        description=(
            "Allow OpenClaw/MCP clients to persist and execute Python harness or policy artifacts in the host "
            "process. Unsafe compatibility escape hatch; keep disabled until an isolated execution backend is used"
        ),
    )
    openclaw_allow_private_network_endpoint: bool = Field(
        default=False,
        description=(
            "Allow the operator-configured OpenClaw HTTP sidecar endpoint to resolve to loopback, private, or "
            "link-local addresses. Cloud metadata endpoints remain blocked"
        ),
    )
