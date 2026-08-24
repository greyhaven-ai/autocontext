"""Settings for live promotion, campaign auditing, and Prime execution."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator  # type: ignore[import-not-found]

from autocontext.runtime_images import PINNED_PYTHON_RUNTIME_IMAGE, require_pinned_runtime_image

_PRIME_SUPPORTED_TELEMETRY = frozenset({"hardware_identity"})


class ProductionExecutionFields(BaseModel):
    """Bounded production-runtime settings kept out of the core config monolith."""

    context_bundle_promotion_enabled: bool = Field(default=False)
    context_bundle_promotion_min_screen_pairs: int = Field(default=2, ge=1)
    context_bundle_promotion_min_confirmation_pairs: int = Field(default=6, ge=2)
    context_bundle_promotion_max_confirmation_pairs: int = Field(default=20, ge=2)
    context_bundle_promotion_min_heldout_pairs: int = Field(default=2, ge=1)
    context_bundle_promotion_min_effect: float = Field(default=0.0, allow_inf_nan=False)
    context_bundle_promotion_confidence_z: float = Field(default=1.96, gt=0.0, allow_inf_nan=False)
    context_bundle_promotion_seed_base: int = Field(
        default=50_000,
        ge=0,
        le=9_007_199_254_740_991,
    )
    context_bundle_promotion_eval_timeout_seconds: float = Field(default=10.0, gt=0.0, allow_inf_nan=False)
    context_bundle_promotion_eval_max_memory_mb: int = Field(default=512, ge=1)
    context_bundle_promotion_familywise_alpha: float = Field(default=0.05, gt=0.0, lt=1.0, allow_inf_nan=False)
    context_bundle_promotion_allocation_decay: float = Field(default=0.5, gt=0.0, lt=1.0, allow_inf_nan=False)
    context_bundle_promotion_min_independent_blocks: int = Field(default=2, ge=2)
    context_bundle_promotion_robust_method: Literal["cluster_t", "bounded_hoeffding"] = "cluster_t"

    primeintellect_api_base: str = Field(
        default="https://api.primeintellect.ai",
        deprecated=(
            "Custom Prime API bases are unsupported by the installed prime-sandboxes SDK constructor; "
            "this compatibility setting accepts only the provider default."
        ),
    )
    primeintellect_api_key: str | None = Field(default=None, repr=False)
    primeintellect_docker_image: str = Field(default=PINNED_PYTHON_RUNTIME_IMAGE)
    primeintellect_cpu_cores: float = Field(default=1.0, ge=0.25)
    primeintellect_memory_gb: float = Field(default=2.0, ge=0.25)
    primeintellect_disk_size_gb: float = Field(default=5.0, ge=1.0)
    primeintellect_accelerator_kind: str = Field(
        default="",
        description="Optional default accelerator kind for non-campaign Prime execution.",
    )
    primeintellect_accelerator_count: int = Field(default=0, ge=0)
    primeintellect_region: str = Field(default="")
    primeintellect_required_telemetry: str = Field(
        default="hardware_identity",
        description="Telemetry required for configured accelerator execution; currently hardware_identity only.",
    )
    primeintellect_supported_accelerator_kinds: str = Field(
        default="",
        description="Comma-separated accelerator kinds explicitly configured as available from Prime.",
    )
    primeintellect_max_accelerator_count: int = Field(default=0, ge=0)
    primeintellect_supported_regions: str = Field(default="")
    primeintellect_supported_images: str = Field(
        default="",
        description="Comma-separated immutable images validated for the configured Prime accelerator pool.",
    )
    primeintellect_available_telemetry: str = Field(
        default="hardware_identity",
        description="Prime telemetry exposed by the SDK integration; currently hardware_identity only.",
    )
    primeintellect_timeout_minutes: int = Field(default=30, ge=1)
    primeintellect_wait_attempts: int = Field(default=60, ge=1)
    primeintellect_max_retries: int = Field(default=2, ge=0)
    primeintellect_backoff_seconds: float = Field(default=0.75, ge=0)
    allow_primeintellect_fallback: bool = Field(default=False, description="Allow an unavailable local sentinel")

    campaign_auditor_enabled: bool = Field(default=False)
    campaign_auditor_provider: str = Field(default="anthropic", min_length=1)
    campaign_auditor_model: str = Field(default="claude-opus-5", min_length=1)
    campaign_auditor_base_url: str = Field(
        default="",
        description="Dedicated auditor endpoint; never inherited from agent or judge routes.",
    )
    campaign_auditor_api_key: str = Field(
        default="",
        repr=False,
        description="Dedicated auditor credential; falls back only to the selected provider's native environment key.",
    )
    campaign_auditor_proposer_provider: str = Field(default="anthropic", min_length=1)
    campaign_auditor_proposer_model: str = Field(default="claude-sonnet-5", min_length=1)
    campaign_auditor_allow_same_route: bool = Field(default=False)
    campaign_auditor_max_calls_per_campaign: int = Field(default=8, ge=1)
    campaign_auditor_max_input_chars: int = Field(default=24_000, ge=1_000)
    campaign_auditor_max_output_tokens: int = Field(default=1_200, ge=64)
    campaign_auditor_timeout_seconds: float = Field(default=30.0, gt=0.0, allow_inf_nan=False)
    campaign_auditor_policy: Literal[
        "advisory",
        "review_required_on_high",
        "pause_recommended_on_critical",
    ] = "advisory"
    campaign_auditor_input_cost_per_million: float = Field(default=0.0, ge=0.0, allow_inf_nan=False)
    campaign_auditor_output_cost_per_million: float = Field(default=0.0, ge=0.0, allow_inf_nan=False)

    @field_validator("primeintellect_docker_image")
    @classmethod
    def _primeintellect_image_is_immutable(cls, value: str) -> str:
        return require_pinned_runtime_image(value)

    @field_validator("primeintellect_api_base")
    @classmethod
    def _primeintellect_api_base_is_supported(cls, value: str) -> str:
        normalized = value.rstrip("/").removesuffix("/api/v1")
        if normalized != "https://api.primeintellect.ai":
            raise ValueError("custom AUTOCONTEXT_PRIMEINTELLECT_API_BASE values are unsupported by prime-sandboxes")
        return "https://api.primeintellect.ai"

    @model_validator(mode="after")
    def _validate_context_bundle_promotion_counts(self) -> Self:
        if self.context_bundle_promotion_max_confirmation_pairs < self.context_bundle_promotion_min_confirmation_pairs:
            raise ValueError(
                "context_bundle_promotion_max_confirmation_pairs must be >= context_bundle_promotion_min_confirmation_pairs"
            )
        supported_kinds = parse_csv_values(self.primeintellect_supported_accelerator_kinds)
        supported_regions = parse_csv_values(self.primeintellect_supported_regions)
        supported_images = parse_csv_values(self.primeintellect_supported_images)
        required_telemetry = parse_csv_values(self.primeintellect_required_telemetry)
        available_telemetry = parse_csv_values(self.primeintellect_available_telemetry)
        unsupported_telemetry = (required_telemetry | available_telemetry) - _PRIME_SUPPORTED_TELEMETRY
        if unsupported_telemetry:
            raise ValueError(f"telemetry is unsupported by the Prime SDK integration: {', '.join(sorted(unsupported_telemetry))}")
        if bool(supported_kinds) != (self.primeintellect_max_accelerator_count > 0):
            raise ValueError(
                "Prime supported accelerator kinds and primeintellect_max_accelerator_count must be configured together"
            )
        kind = self.primeintellect_accelerator_kind.strip()
        if bool(kind) != (self.primeintellect_accelerator_count > 0):
            raise ValueError("Prime accelerator kind and count must be configured together")
        if supported_images and self.primeintellect_docker_image not in supported_images:
            raise ValueError("Prime docker image is not in primeintellect_supported_images")
        region = self.primeintellect_region.strip()
        if region and region not in supported_regions:
            raise ValueError("Prime region is not in primeintellect_supported_regions")
        if kind:
            if kind not in supported_kinds:
                raise ValueError("Prime accelerator kind is not in primeintellect_supported_accelerator_kinds")
            if self.primeintellect_accelerator_count > self.primeintellect_max_accelerator_count:
                raise ValueError("Prime accelerator count exceeds primeintellect_max_accelerator_count")
            if not supported_images:
                raise ValueError("Prime accelerator execution requires primeintellect_supported_images")
            if "hardware_identity" not in required_telemetry:
                raise ValueError("Prime accelerator execution must require hardware_identity telemetry")
            missing_telemetry = required_telemetry - available_telemetry
            if missing_telemetry:
                raise ValueError(f"Prime required telemetry is unavailable: {', '.join(sorted(missing_telemetry))}")
        return self


def parse_csv_values(value: str) -> frozenset[str]:
    return frozenset(item.strip() for item in value.split(",") if item.strip())


__all__ = ["ProductionExecutionFields", "parse_csv_values"]
