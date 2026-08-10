"""Settings for provider routing and per-role endpoint overrides."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field  # type: ignore[import-not-found]

CapabilityDeclaration = Literal["", "fast", "mid_tier", "frontier"]
HostingDeclaration = Literal["", "local", "remote"]


class RoleRoutingFields(BaseModel):
    """Capability, hosting, and connection settings used by role routing."""

    role_routing: Literal["off", "auto"] = Field(default="off", description="Role routing mode")
    provider_capability: CapabilityDeclaration = Field(
        default="",
        description="Default endpoint capability: frontier, mid_tier, or fast",
    )
    provider_hosting: HostingDeclaration = Field(
        default="",
        description="Default endpoint hosting: local or remote; empty infers from transport",
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
