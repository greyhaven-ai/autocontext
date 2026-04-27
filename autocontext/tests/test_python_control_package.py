from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PY_CONTROL_SRC = REPO_ROOT / "packages" / "python" / "control" / "src"
if str(PY_CONTROL_SRC) not in sys.path:
    sys.path.insert(0, str(PY_CONTROL_SRC))

control_package = import_module("autocontext_control")
package_role = control_package.package_role
package_topology_version = control_package.package_topology_version


def test_python_control_package_identity() -> None:
    assert package_role == "control"
    assert package_topology_version == 1


def test_python_control_reexports_research_domain_contracts() -> None:
    Citation = control_package.Citation
    ResearchAdapter = control_package.ResearchAdapter
    ResearchConfig = control_package.ResearchConfig
    ResearchQuery = control_package.ResearchQuery
    ResearchResult = control_package.ResearchResult
    Urgency = control_package.Urgency

    query = ResearchQuery(
        topic="refund policy changes",
        context="customer support escalation",
        urgency=Urgency.HIGH,
        max_results=3,
        constraints=["cite primary sources"],
        scenario_family="agent_task",
        metadata={"ticket": "t-1"},
    )
    citation = Citation(
        source="policy handbook",
        url="https://example.com/policy",
        relevance=0.95,
        snippet="Refunds require manager sign-off after 30 days.",
        retrieved_at="2026-04-25T00:00:00Z",
    )

    class DemoResearchAdapter:
        def search(self, query: ResearchQuery) -> ResearchResult:
            return ResearchResult(
                query_topic=query.topic,
                summary="Manager sign-off required after 30 days.",
                citations=[citation],
                confidence=0.91,
                metadata={"adapter": "demo"},
            )

    adapter = DemoResearchAdapter()
    result = adapter.search(query)
    config = ResearchConfig(enabled=True, adapter_name="demo", max_queries_per_turn=1)

    assert query.urgency is Urgency.HIGH
    assert query.constraints == ["cite primary sources"]
    assert isinstance(adapter, ResearchAdapter)
    assert result.has_citations is True
    assert result.citations[0].source == "policy handbook"
    assert result.metadata == {"adapter": "demo"}
    assert config.enabled is True
    assert config.adapter_name == "demo"
    assert config.max_queries_per_turn == 1


def test_python_control_reexports_research_brief() -> None:
    Citation = control_package.Citation
    ResearchBrief = control_package.ResearchBrief
    ResearchResult = control_package.ResearchResult

    citation = Citation(
        source="policy handbook",
        url="https://example.com/policy",
        relevance=0.95,
        snippet="Refunds require manager sign-off after 30 days.",
        retrieved_at="2026-04-25T00:00:00Z",
    )
    strong_result = ResearchResult(
        query_topic="refund policy",
        summary="Manager sign-off required after 30 days.",
        citations=[citation],
        confidence=0.91,
        metadata={"adapter": "demo"},
    )
    weak_result = ResearchResult(
        query_topic="escalation policy",
        summary="Escalate unusual refund cases.",
        citations=[citation],
        confidence=0.42,
        metadata={"adapter": "demo"},
    )

    brief = ResearchBrief.from_results(
        goal="Summarize refund policy changes",
        results=[strong_result, weak_result],
        min_confidence=0.9,
    )

    assert brief.goal == "Summarize refund policy changes"
    assert len(brief.findings) == 1
    assert brief.findings[0].query_topic == "refund policy"
    assert len(brief.unique_citations) == 1
    assert brief.unique_citations[0].source == "policy handbook"
    assert brief.avg_confidence == 0.91
    assert "Research Brief: Summarize refund policy changes" in brief.to_markdown()


def test_python_control_reexports_production_trace_contracts() -> None:
    Chosen = control_package.Chosen
    EndedAt = control_package.EndedAt
    EnvContext = control_package.EnvContext
    Error = control_package.Error
    FeedbackRef = control_package.FeedbackRef
    Items = control_package.Items
    Message = control_package.Message
    ProductionOutcome = control_package.ProductionOutcome
    ProductionTrace = control_package.ProductionTrace
    Provider = control_package.Provider
    RedactionMarker = control_package.RedactionMarker
    Routing = control_package.Routing
    Sdk = control_package.Sdk
    SessionIdentifier = control_package.SessionIdentifier
    TimingInfo = control_package.TimingInfo
    ToolCall = control_package.ToolCall
    TraceLinks = control_package.TraceLinks
    TraceSource = control_package.TraceSource
    UsageInfo = control_package.UsageInfo

    sdk = Sdk(name="autoctx", version="0.1.0")
    source = TraceSource(emitter="gateway", sdk=sdk, hostname="box-1")
    provider = Provider(name="anthropic", endpoint="https://api.anthropic.com", providerVersion="2026-04")
    env = EnvContext(
        environmentTag="prod",
        appId="support-bot",
        taskType="triage",
        deploymentMeta={"region": "us-east-1"},
    )
    session = SessionIdentifier(
        userIdHash="a" * 64,
        sessionIdHash="b" * 64,
        requestId="req-1",
    )
    message = Message(
        role="user",
        content="help me with a refund",
        timestamp="2026-04-25T00:00:00Z",
        toolCalls=[Items(toolName="kb.search", args={"query": "refund"}, durationMs=12.0)],
        metadata={"lang": "en"},
    )
    tool_call = ToolCall(
        toolName="kb.search",
        args={"query": "refund"},
        result={"hits": 1},
        durationMs=12.0,
    )
    outcome = ProductionOutcome(
        label="success",
        score=0.9,
        reasoning="resolved",
        signals={"accuracy": 0.9},
        error=Error(type="none", message="no error"),
    )
    timing = TimingInfo(
        startedAt="2026-04-25T00:00:00Z",
        endedAt="2026-04-25T00:00:01Z",
        latencyMs=1000.0,
    )
    usage = UsageInfo(tokensIn=10, tokensOut=5, estimatedCostUsd=0.01)
    feedback = FeedbackRef(
        kind="rating",
        submittedAt="2026-04-25T00:00:02Z",
        ref="feedback-1",
        score=0.9,
        comment="great help",
    )
    links = TraceLinks(
        scenarioId="grid_ctf",
        runId="run-1",
        evalExampleIds=["eval-1"],
        trainingRecordIds=["train-1"],
    )
    redaction = RedactionMarker(
        path="messages[0].content",
        reason="pii-name",
        detectedBy="operator",
        detectedAt="2026-04-25T00:00:03Z",
    )
    routing = Routing(
        chosen=Chosen(
            provider="anthropic",
            model="claude-sonnet",
            endpoint="https://api.anthropic.com",
        ),
        matchedRouteId="route-1",
        reason="matched-route",
        evaluatedAt="2026-04-25T00:00:04Z",
    )

    trace = ProductionTrace(
        schemaVersion="1.0",
        traceId="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        source=source,
        provider=provider,
        model="claude-sonnet",
        session=session,
        env=env,
        messages=[message],
        toolCalls=[tool_call],
        outcome=outcome,
        timing=timing,
        usage=usage,
        feedbackRefs=[feedback],
        links=links,
        redactions=[redaction],
        routing=routing,
        metadata={"run": "r1"},
    )
    recreated_trace = ProductionTrace.model_validate(trace.model_dump())

    assert trace.model == "claude-sonnet"
    assert trace.source.sdk.name == "autoctx"
    assert trace.messages[0].toolCalls[0].toolName == "kb.search"
    assert trace.feedbackRefs[0].ref == "feedback-1"
    assert recreated_trace.routing.reason == "matched-route"
    assert EndedAt.model_validate(trace.messages[0].timestamp).root.isoformat().startswith("2026-04-25")
