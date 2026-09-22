"""Opt-in pure-task routing contracts; lifecycle remains in ContextBundleStore."""

from __future__ import annotations

import math
from decimal import ROUND_CEILING, Decimal, localcontext
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from autocontext.artifacts.policy_candidate import FrozenContract
from autocontext.context_bundles.models import stable_digest
from autocontext.harness.cost.calculator import CostCalculator
from autocontext.harness.cost.types import ModelPricing

Route = Literal["skill", "specialized", "general", "override"]


class ModelTarget(FrozenContract):
    """Operator-pinned single-dispatch endpoint and conservative billing bounds.

    Rates and the input-token ceiling are operator assertions about the backend,
    not inferred prices. Providers without such bounds are ineligible.
    """

    provider: Literal["openai-compatible", "anthropic"]
    model: str = Field(min_length=1, max_length=256)
    base_url: str | None = Field(default=None, max_length=2048)
    api_key_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,100}_(?:API_KEY|TOKEN)$")
    max_input_tokens: int = Field(default=8192, ge=1, le=131072, strict=True)
    max_output_tokens: int = Field(default=1024, ge=1, le=8192, strict=True)
    input_cost_per_1k: float = Field(ge=0, strict=True)
    output_cost_per_1k: float = Field(ge=0, strict=True)
    timeout_seconds: float = Field(default=10, gt=0, le=60, strict=True)

    @model_validator(mode="after")
    def endpoint(self) -> ModelTarget:
        if self.provider == "anthropic" and self.base_url is not None:
            raise ValueError("Anthropic uses its fixed API endpoint")
        parsed = urlsplit(self.resolved_endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("an HTTP endpoint without embedded credentials is required")
        if parsed.query or parsed.fragment:
            raise ValueError("endpoint query/fragment is unsupported")
        return self

    @property
    def resolved_endpoint(self) -> str:
        return self.base_url or ("https://api.anthropic.com" if self.provider == "anthropic" else "https://api.openai.com/v1")

    @property
    def digest(self) -> str:
        return stable_digest(self.model_dump(mode="json"))

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        pricing = ModelPricing(self.model, self.input_cost_per_1k, self.output_cost_per_1k)
        estimate = CostCalculator(pricing=[pricing], default=pricing).calculate(
            self.model, input_tokens, output_tokens).total_cost
        # The shared calculator rounds reports to the nearest microdollar. A
        # hard admission budget must instead reserve upward, including tiny rates.
        if not math.isfinite(estimate):
            return estimate
        with localcontext() as context:
            context.prec = 340
            exact = (Decimal(str(self.input_cost_per_1k)) * input_tokens
                     + Decimal(str(self.output_cost_per_1k)) * output_tokens) / 1000
            upper = float(exact.quantize(Decimal("0.000001"), rounding=ROUND_CEILING))
        return max(estimate, upper)


class RoutingBudget(FrozenContract):
    wall_seconds: float = Field(default=30, gt=0, le=180, strict=True)
    max_attempts: int = Field(default=3, ge=0, le=3, strict=True)
    max_tokens: int = Field(default=20000, ge=0, le=1000000, strict=True)
    max_model_cost_usd: float = Field(default=1, ge=0, strict=True)


class SkillRoutingConfig(FrozenContract):
    enabled: bool = Field(default=False, strict=True)
    allow_network: bool = Field(default=False, strict=True)
    bundle_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    specialized: ModelTarget | None = None
    specialized_backend: str | None = Field(default=None, pattern=r"^[a-z0-9-]{1,64}$")
    general: ModelTarget | None = None
    override: ModelTarget | None = None
    learned_playbook: str = Field(default="", max_length=8192, strict=True)
    budget: RoutingBudget = Field(default_factory=RoutingBudget)

    @model_validator(mode="after")
    def valid_playbook(self) -> SkillRoutingConfig:
        self.learned_playbook.encode("utf-8", errors="strict")
        return self

    @property
    def digest(self) -> str:
        return stable_digest(self.model_dump(mode="json"))


class RouteAttempt(FrozenContract):
    route: Route
    status: Literal["started", "skipped", "abstention", "execution_failure", "verification_failure", "success"]
    reason: str
    artifact_digest: str | None = None
    registered_artifact_id: str | None = None
    model: str | None = None
    reported_model: str | None = None
    target_digest: str | None = None
    environment_digest: str | None = None
    elapsed_seconds: float = 0
    model_calls: int = 0
    reserved_tokens: int = 0
    reserved_cost_usd: float = 0
    reported_tokens: int | None = None
    accounted_tokens: int = 0
    accounted_cost_usd: float = 0
    reported_cost_usd: float | None = None
    accounting: Literal["none", "reservation", "declared-pricing", "provider-reported"] = "none"


class SkillRoutingResult(FrozenContract):
    schema_version: Literal["autocontext.skill-routing.v1"] = "autocontext.skill-routing.v1"
    request_id: str
    status: Literal["started", "success", "abstention", "execution_failure"]
    reason: str
    selected_route: Route | None = None
    output_json: str | None = None
    input_json: str | None = None
    input_sha256: str | None = None
    config_json: str
    config_digest: str
    evaluator_identity: str
    mode: Literal["evaluation", "serving"]
    attempts: tuple[RouteAttempt, ...] = ()
    elapsed_seconds: float = 0
    model_calls: int = 0
    accounted_tokens: int = 0
    accounted_model_cost_usd: float = 0
    # Reservations and declared prices are not an invoice or total lifecycle cost.
    model_cost_complete: bool = True
    trace_path: str
