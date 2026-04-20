# AUTO-GENERATED from ts/src/production-traces/contract/json-schemas/ — DO NOT EDIT.
# Run: node ts/scripts/sync-python-production-traces-schemas.mjs
# CI gate: node ts/scripts/sync-python-production-traces-schemas.mjs --check

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, RootModel


class Sdk(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    name: Annotated[str, Field(min_length=1)]
    version: Annotated[str, Field(min_length=1)]


class TraceSource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    emitter: Annotated[str, Field(min_length=1)]
    sdk: Sdk
    hostname: str | None = None


class Provider(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    name: Literal[
        'openai',
        'anthropic',
        'openai-compatible',
        'langchain',
        'vercel-ai-sdk',
        'litellm',
        'other',
    ]
    endpoint: str | None = None
    providerVersion: str | None = None


class EnvContext(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    environmentTag: Annotated[str, Field(pattern='^[a-zA-Z0-9][a-zA-Z0-9_-]*$')]
    appId: Annotated[str, Field(pattern='^[a-z0-9][a-z0-9_-]*$')]
    taskType: Annotated[str | None, Field(min_length=1)] = None
    deploymentMeta: dict[str, Any] | None = None


class ToolCall(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    toolName: Annotated[str, Field(min_length=1)]
    args: dict[str, Any]
    result: Any | None = None
    durationMs: Annotated[float | None, Field(ge=0.0)] = None
    error: str | None = None


class Error(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    type: Annotated[str, Field(min_length=1)]
    message: str
    stack: str | None = None


class ProductionOutcome(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    label: Literal['success', 'failure', 'partial', 'unknown'] | None = None
    score: Annotated[float | None, Field(ge=0.0, le=1.0)] = None
    reasoning: str | None = None
    signals: dict[str, float] | None = None
    error: Error | None = None


class UsageInfo(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    tokensIn: Annotated[int, Field(ge=0)]
    tokensOut: Annotated[int, Field(ge=0)]
    estimatedCostUsd: Annotated[float | None, Field(ge=0.0)] = None
    providerUsage: dict[str, Any] | None = None


class EvalExampleId(RootModel[str]):
    root: Annotated[str, Field(min_length=1)]


class TrainingRecordId(RootModel[str]):
    root: Annotated[str, Field(min_length=1)]


class TraceLinks(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    scenarioId: Annotated[str | None, Field(pattern='^[a-z0-9][a-z0-9_-]*$')] = None
    runId: Annotated[str | None, Field(min_length=1)] = None
    evalExampleIds: list[EvalExampleId] | None = None
    trainingRecordIds: list[TrainingRecordId] | None = None


class Chosen(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    provider: Annotated[str, Field(min_length=1)]
    model: Annotated[str, Field(min_length=1)]
    endpoint: str | None = None


class Routing(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    chosen: Chosen
    matchedRouteId: Annotated[str | None, Field(min_length=1)] = None
    reason: Literal['default', 'matched-route', 'fallback']
    fallbackReason: (
        Literal['budget-exceeded', 'latency-breached', 'provider-error', 'no-match']
        | None
    ) = None
    evaluatedAt: AwareDatetime


class UserIdHash(RootModel[str]):
    root: Annotated[str, Field(pattern='^[0-9a-f]{64}$')]


class EndedAt(RootModel[AwareDatetime]):
    root: AwareDatetime


class Items(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    toolName: Annotated[str, Field(min_length=1)]
    args: dict[str, Any]
    result: Any | None = None
    durationMs: Annotated[float | None, Field(ge=0.0)] = None
    error: str | None = None


class SessionIdentifier(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    userIdHash: Annotated[str | None, Field(pattern='^[0-9a-f]{64}$')] = None
    sessionIdHash: UserIdHash | None = None
    requestId: Annotated[str | None, Field(min_length=1)] = None


class Message(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    role: Literal['user', 'assistant', 'system', 'tool']
    content: str
    timestamp: EndedAt
    toolCalls: list[Items] | None = None
    metadata: dict[str, Any] | None = None


class TimingInfo(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    startedAt: EndedAt
    endedAt: AwareDatetime
    latencyMs: Annotated[float, Field(ge=0.0)]
    timeToFirstTokenMs: Annotated[float | None, Field(ge=0.0)] = None


class FeedbackRef(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    kind: Literal['thumbs', 'rating', 'correction', 'edit', 'custom']
    submittedAt: EndedAt
    ref: Annotated[str, Field(min_length=1)]
    score: float | None = None
    comment: str | None = None


class RedactionMarker(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    path: Annotated[str, Field(min_length=1)]
    reason: Literal['pii-email', 'pii-name', 'pii-ssn', 'secret-token', 'pii-custom']
    category: str | None = None
    detectedBy: Literal['client', 'ingestion', 'operator']
    detectedAt: EndedAt


class ProductionTrace(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    schemaVersion: Literal['1.0']
    traceId: Annotated[str, Field(pattern='^[0-9A-HJKMNP-TV-Z]{26}$')]
    source: Annotated[TraceSource, Field(title='TraceSource')]
    provider: Provider
    model: Annotated[str, Field(min_length=1)]
    session: Annotated[SessionIdentifier | None, Field(title='SessionIdentifier')] = (
        None
    )
    env: Annotated[EnvContext, Field(title='EnvContext')]
    messages: Annotated[list[Message], Field(min_length=1)]
    toolCalls: list[ToolCall]
    outcome: Annotated[ProductionOutcome | None, Field(title='ProductionOutcome')] = (
        None
    )
    timing: Annotated[TimingInfo, Field(title='TimingInfo')]
    usage: Annotated[UsageInfo, Field(title='UsageInfo')]
    feedbackRefs: list[FeedbackRef]
    links: Annotated[TraceLinks, Field(title='TraceLinks')]
    redactions: list[RedactionMarker]
    routing: Routing | None = None
    metadata: dict[str, Any] | None = None
