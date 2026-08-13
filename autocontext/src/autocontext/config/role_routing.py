"""Settings for provider routing and per-role endpoint overrides."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field  # type: ignore[import-not-found]

CapabilityDeclaration = Literal["", "fast", "mid_tier", "frontier"]
HostingDeclaration = Literal["", "local", "remote"]


class RoleRoutingFields(BaseModel):
    """Capability, hosting, and connection settings used by role routing."""

    role_routing: Literal["off", "auto"] = Field(default="off", description="Role routing mode")
    # AC-931: the escape hatch. Constrained decoding is on by default because it
    # removes a silent failure mode (AC-913 measured 100% -> 0% section loss on
    # an open-weight model). It is switchable because the OpenAI-compatible
    # backends it changes include cloud models whose analysis quality we could
    # not measure, and "pin to 0.14" is not an acceptable answer for someone who
    # sees a regression.
    constrained_output: bool = Field(
        default=True,
        description="Ask backends to constrain role output to its schema; off falls back to markdown parsing",
    )
    provider_capability: CapabilityDeclaration = Field(
        default="",
        description="Default endpoint capability: frontier, mid_tier, or fast",
    )
    provider_hosting: HostingDeclaration = Field(
        default="",
        description="Default endpoint hosting: local or remote; empty infers from transport",
    )
    # AC-917: when true, the engine never initiates an outbound connection.
    # Scoped by who initiates: operator-initiated access (SSH, a tunnel) is out
    # of scope, so "airgapped" does not have to mean "unreachable".
    offline: bool = Field(
        default=False,
        description="Refuse all engine-initiated network egress (AUTOCONTEXT_OFFLINE)",
    )

    competitor_provider: str = Field(default="", description="Provider override for competitor role")
    analyst_provider: str = Field(default="", description="Provider override for analyst role")
    coach_provider: str = Field(default="", description="Provider override for coach role")
    architect_provider: str = Field(default="", description="Provider override for architect role")

    competitor_provider_capability: CapabilityDeclaration = Field(default="")
    analyst_provider_capability: CapabilityDeclaration = Field(default="")
    coach_provider_capability: CapabilityDeclaration = Field(default="")
    architect_provider_capability: CapabilityDeclaration = Field(default="")
    competitor_provider_hosting: HostingDeclaration = Field(default="")
    analyst_provider_hosting: HostingDeclaration = Field(default="")
    coach_provider_hosting: HostingDeclaration = Field(default="")
    architect_provider_hosting: HostingDeclaration = Field(default="")

    competitor_api_key: str = Field(default="", description="API key override for competitor role")
    competitor_base_url: str = Field(default="", description="Base URL override for competitor role")
    analyst_api_key: str = Field(default="", description="API key override for analyst role")
    analyst_base_url: str = Field(default="", description="Base URL override for analyst role")
    coach_api_key: str = Field(default="", description="API key override for coach role")
    coach_base_url: str = Field(default="", description="Base URL override for coach role")
    architect_api_key: str = Field(default="", description="API key override for architect role")
    architect_base_url: str = Field(default="", description="Base URL override for architect role")
