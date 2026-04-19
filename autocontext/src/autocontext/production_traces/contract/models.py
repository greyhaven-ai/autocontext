"""Pydantic v2 models for production-trace documents.

.. note::

   AUTO-GENERATED-MIRROR of the canonical JSON Schemas under
   ``ts/src/production-traces/contract/json-schemas/``. This module is the
   Python-side projection of the same contract the TS AJV validator enforces.

   ``datamodel-code-generator`` cannot follow the absolute-URL ``$ref`` style
   used by the canonical schemas (matching Foundation B's convention) without
   fetching them over HTTP. Until the sync script materializes a local-ref
   bundle, we hand-maintain these models. Cross-runtime compatibility is
   property-tested against shared fixtures — any drift between this file and
   the JSON Schemas will be caught by the cross-runtime test suite.

Regenerate via: ``node ts/scripts/sync-python-production-traces-schemas.mjs``.
CI gate: ``node ts/scripts/sync-python-production-traces-schemas.mjs --check``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat, NonNegativeInt

from autocontext.production_traces.contract.branded_ids import (
    AppId,
    EnvironmentTag,
    ProductionTraceId,
    Scenario,
    SessionIdHash,
    UserIdHash,
)
from autocontext.production_traces.contract.branded_ids import (
    ContentHash as _ContentHash,  # noqa: F401 — re-exported for downstream use
)

# --- Shared primitives -----------------------------------------------------

MessageRole = Literal["user", "assistant", "system", "tool"]
ProviderName = Literal[
    "openai",
    "anthropic",
    "openai-compatible",
    "langchain",
    "vercel-ai-sdk",
    "litellm",
    "other",
]
OutcomeLabel = Literal["success", "failure", "partial", "unknown"]
FeedbackKind = Literal["thumbs", "rating", "correction", "edit", "custom"]
RedactionReason = Literal["pii-email", "pii-name", "pii-ssn", "secret-token", "pii-custom"]
DetectedBy = Literal["client", "ingestion", "operator"]
SchemaVersion10 = Literal["1.0"]


class _Strict(BaseModel):
    """Base model: forbid unknown fields (mirrors JSON Schema ``additionalProperties: false``)."""

    model_config = ConfigDict(extra="forbid", frozen=False, strict=False)


class ToolCall(_Strict):
    toolName: Annotated[str, Field(min_length=1)]
    args: dict[str, Any]
    result: Any | None = None
    durationMs: NonNegativeFloat | None = None
    error: str | None = None


class TraceMessage(_Strict):
    role: MessageRole
    content: str
    timestamp: str
    toolCalls: list[ToolCall] | None = None
    metadata: dict[str, Any] | None = None


# --- Sub-aggregates --------------------------------------------------------


class _Sdk(_Strict):
    name: Annotated[str, Field(min_length=1)]
    version: Annotated[str, Field(min_length=1)]


class TraceSource(_Strict):
    emitter: Annotated[str, Field(min_length=1)]
    sdk: _Sdk
    hostname: str | None = None


class ProviderInfo(_Strict):
    name: ProviderName
    endpoint: str | None = None
    providerVersion: str | None = None


class SessionIdentifier(_Strict):
    userIdHash: UserIdHash | None = None
    sessionIdHash: SessionIdHash | None = None
    requestId: Annotated[str, Field(min_length=1)] | None = None


class EnvContext(_Strict):
    environmentTag: EnvironmentTag
    appId: AppId
    taskType: Annotated[str, Field(min_length=1)] | None = None
    deploymentMeta: dict[str, Any] | None = None


class TimingInfo(_Strict):
    startedAt: str
    endedAt: str
    latencyMs: NonNegativeFloat
    timeToFirstTokenMs: NonNegativeFloat | None = None


class UsageInfo(_Strict):
    tokensIn: NonNegativeInt
    tokensOut: NonNegativeInt
    estimatedCostUsd: NonNegativeFloat | None = None
    providerUsage: dict[str, Any] | None = None


class _OutcomeError(_Strict):
    type: Annotated[str, Field(min_length=1)]
    message: str
    stack: str | None = None


class ProductionOutcome(_Strict):
    label: OutcomeLabel | None = None
    score: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    reasoning: str | None = None
    signals: dict[str, float] | None = None
    error: _OutcomeError | None = None


class FeedbackRef(_Strict):
    kind: FeedbackKind
    submittedAt: str
    ref: Annotated[str, Field(min_length=1)]
    score: float | None = None
    comment: str | None = None


class TraceLinks(_Strict):
    scenarioId: Scenario | None = None
    runId: Annotated[str, Field(min_length=1)] | None = None
    evalExampleIds: list[Annotated[str, Field(min_length=1)]] | None = None
    trainingRecordIds: list[Annotated[str, Field(min_length=1)]] | None = None


class RedactionMarker(_Strict):
    path: Annotated[str, Field(min_length=1)]
    reason: RedactionReason
    category: str | None = None
    detectedBy: DetectedBy
    detectedAt: str


# --- Aggregate root --------------------------------------------------------


class ProductionTrace(_Strict):
    schemaVersion: SchemaVersion10
    traceId: ProductionTraceId
    source: TraceSource
    provider: ProviderInfo
    model: Annotated[str, Field(min_length=1)]
    session: SessionIdentifier | None = None
    env: EnvContext
    messages: Annotated[list[TraceMessage], Field(min_length=1)]
    toolCalls: list[ToolCall]
    outcome: ProductionOutcome | None = None
    timing: TimingInfo
    usage: UsageInfo
    feedbackRefs: list[FeedbackRef]
    links: TraceLinks
    redactions: list[RedactionMarker]
    metadata: dict[str, Any] | None = None


__all__ = [
    "DetectedBy",
    "EnvContext",
    "FeedbackKind",
    "FeedbackRef",
    "MessageRole",
    "OutcomeLabel",
    "ProductionOutcome",
    "ProductionTrace",
    "ProviderInfo",
    "ProviderName",
    "RedactionMarker",
    "RedactionReason",
    "SchemaVersion10",
    "SessionIdentifier",
    "TimingInfo",
    "ToolCall",
    "TraceLinks",
    "TraceMessage",
    "TraceSource",
    "UsageInfo",
]
