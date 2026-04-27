from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

from pydantic import ValidationError

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


def test_python_control_reexports_shared_server_protocol_models() -> None:
    ExecutorInfo = control_package.ExecutorInfo
    ExecutorResources = control_package.ExecutorResources
    PROTOCOL_VERSION = control_package.PROTOCOL_VERSION
    ScenarioInfo = control_package.ScenarioInfo
    ScoringComponent = control_package.ScoringComponent
    StrategyParam = control_package.StrategyParam

    scenario = ScenarioInfo(name="grid_ctf", description="Capture the flag")
    resources = ExecutorResources(
        docker_image="ghcr.io/greyhaven/executor:latest",
        cpu_cores=4,
        memory_gb=8,
        disk_gb=20,
        timeout_minutes=15,
    )
    executor = ExecutorInfo(
        mode="docker",
        available=True,
        description="Local Docker executor",
        resources=resources,
    )
    param = StrategyParam(name="aggression", description="How aggressively to pursue flags")
    scoring = ScoringComponent(name="win_rate", description="Percent of matches won", weight=0.7)

    assert PROTOCOL_VERSION == 1
    assert scenario.name == "grid_ctf"
    assert executor.resources.cpu_cores == 4
    assert param.name == "aggression"
    assert scoring.weight == 0.7


def test_python_control_reexports_environment_discovery_messages() -> None:
    EnvironmentsMsg = control_package.EnvironmentsMsg
    ExecutorInfo = control_package.ExecutorInfo
    ExecutorResources = control_package.ExecutorResources
    ScenarioInfo = control_package.ScenarioInfo

    environments = EnvironmentsMsg(
        scenarios=[
            ScenarioInfo(name="grid_ctf", description="Capture the flag"),
            ScenarioInfo(name="schema_repair", description="Recover a schema from examples."),
        ],
        executors=[
            ExecutorInfo(
                mode="docker",
                available=True,
                description="Local Docker executor",
                resources=ExecutorResources(
                    docker_image="ghcr.io/greyhaven/executor:latest",
                    cpu_cores=4,
                    memory_gb=8,
                    disk_gb=20,
                    timeout_minutes=15,
                ),
            ),
        ],
        current_executor="docker",
        agent_provider="pi",
    )

    assert environments.type == "environments"
    assert environments.scenarios[1].name == "schema_repair"
    assert environments.executors[0].resources is not None
    assert environments.executors[0].resources.cpu_cores == 4
    assert environments.current_executor == "docker"
    assert environments.agent_provider == "pi"


def test_python_control_reexports_run_acceptance_messages() -> None:
    RunAcceptedMsg = control_package.RunAcceptedMsg

    accepted = RunAcceptedMsg(run_id="run-123", scenario="schema_repair", generations=4)

    assert accepted.type == "run_accepted"
    assert accepted.run_id == "run-123"
    assert accepted.scenario == "schema_repair"
    assert accepted.generations == 4


def test_python_control_reexports_chat_response_messages() -> None:
    ChatResponseMsg = control_package.ChatResponseMsg

    response = ChatResponseMsg(role="assistant", text="Schema looks valid.")

    assert response.type == "chat_response"
    assert response.role == "assistant"
    assert response.text == "Schema looks valid."


def test_python_control_reexports_event_messages() -> None:
    EventMsg = control_package.EventMsg

    event = EventMsg(event="run_progress", payload={"run_id": "run-123", "percent": 50})

    assert event.type == "event"
    assert event.event == "run_progress"
    assert event.payload == {"run_id": "run-123", "percent": 50}


def test_python_control_reexports_basic_server_protocol_messages() -> None:
    AckMsg = control_package.AckMsg
    ErrorMsg = control_package.ErrorMsg
    HelloMsg = control_package.HelloMsg
    StateMsg = control_package.StateMsg

    hello = HelloMsg()
    state = StateMsg(paused=True, generation=3, phase="evaluation")
    ack = AckMsg(action="pause", decision="accepted")
    error = ErrorMsg(message="run failed")

    assert hello.type == "hello"
    assert hello.protocol_version == control_package.PROTOCOL_VERSION
    assert state.paused is True
    assert state.generation == 3
    assert state.phase == "evaluation"
    assert ack.action == "pause"
    assert ack.decision == "accepted"
    assert error.type == "error"
    assert error.message == "run failed"


def test_python_control_reexports_monitor_alert_messages() -> None:
    MonitorAlertMsg = control_package.MonitorAlertMsg

    alert = MonitorAlertMsg(
        alert_id="alert-1",
        condition_id="cond-1",
        condition_name="stalled-run",
        condition_type="stall_window",
        scope="run:run-123",
        detail="No events for 30.0s (timeout=30.0s)",
    )

    assert alert.type == "monitor_alert"
    assert alert.condition_name == "stalled-run"
    assert alert.detail == "No events for 30.0s (timeout=30.0s)"


def test_python_control_reexports_monitor_domain_value_objects() -> None:
    ConditionType = control_package.ConditionType
    MonitorAlert = control_package.MonitorAlert
    MonitorCondition = control_package.MonitorCondition

    condition = MonitorCondition(
        id="cond-1",
        name="stall-window",
        condition_type=ConditionType.STALL_WINDOW,
        params={"window": 3},
        scope="run:run-123",
        created_at="2026-04-25T00:00:00Z",
    )
    alert = MonitorAlert(
        id="alert-1",
        condition_id=condition.id,
        condition_name=condition.name,
        condition_type=condition.condition_type,
        scope=condition.scope,
        detail="3 consecutive rollbacks",
        fired_at="2026-04-25T00:01:00Z",
        payload={"window": 3},
    )

    assert ConditionType.STALL_WINDOW == "stall_window"
    assert condition.condition_type is ConditionType.STALL_WINDOW
    assert condition.params == {"window": 3}
    assert alert.condition_type is ConditionType.STALL_WINDOW
    assert alert.detail == "3 consecutive rollbacks"
    assert alert.payload == {"window": 3}


def test_python_control_reexports_agent_contract_dataclasses() -> None:
    AnalystOutput = control_package.AnalystOutput
    ArchitectOutput = control_package.ArchitectOutput
    CoachOutput = control_package.CoachOutput
    CompetitorOutput = control_package.CompetitorOutput

    competitor = CompetitorOutput(
        raw_text="Use beam search.",
        strategy={"approach": "beam-search"},
        reasoning="It keeps more candidate programs alive.",
        is_code_strategy=True,
    )
    analyst = AnalystOutput(
        raw_markdown="# Findings",
        findings=["plateau detected"],
        root_causes=["search space too narrow"],
        recommendations=["increase branching"],
    )
    coach = CoachOutput(
        raw_markdown="# Coaching",
        playbook="Try wider exploration.",
        lessons="Diversity matters.",
        hints="Look for alternate decompositions.",
    )
    architect = ArchitectOutput(
        raw_markdown="# Architecture",
        tool_specs=[{"name": "scratchpad"}],
        harness_specs=[{"id": "h1"}],
        changelog_entry="Added scratchpad tool.",
    )

    assert competitor.is_code_strategy is True
    assert competitor.strategy == {"approach": "beam-search"}
    assert analyst.findings == ["plateau detected"]
    assert analyst.root_causes == ["search space too narrow"]
    assert coach.playbook == "Try wider exploration."
    assert coach.hints == "Look for alternate decompositions."
    assert architect.tool_specs == [{"name": "scratchpad"}]
    assert architect.harness_specs == [{"id": "h1"}]
    assert architect.changelog_entry == "Added scratchpad tool."


def test_python_control_reexports_stagnation_report() -> None:
    StagnationReport = control_package.StagnationReport

    report = StagnationReport(
        is_stagnated=True,
        trigger="score_plateau",
        detail="score variance 0.000001 < epsilon 0.01 over last 5 gens",
    )

    assert report.is_stagnated is True
    assert report.trigger == "score_plateau"
    assert report.detail == "score variance 0.000001 < epsilon 0.01 over last 5 gens"


def test_python_control_reexports_basic_client_control_commands() -> None:
    InjectHintCmd = control_package.InjectHintCmd
    OverrideGateCmd = control_package.OverrideGateCmd
    PauseCmd = control_package.PauseCmd
    ResumeCmd = control_package.ResumeCmd

    pause = PauseCmd()
    resume = ResumeCmd()
    inject_hint = InjectHintCmd(text="Try broader search.")
    override_gate = OverrideGateCmd(decision="retry")

    assert pause.type == "pause"
    assert resume.type == "resume"
    assert inject_hint.type == "inject_hint"
    assert inject_hint.text == "Try broader search."
    assert override_gate.type == "override_gate"
    assert override_gate.decision == "retry"


def test_python_control_reexports_scenario_authoring_commands() -> None:
    CancelScenarioCmd = control_package.CancelScenarioCmd
    ConfirmScenarioCmd = control_package.ConfirmScenarioCmd
    CreateScenarioCmd = control_package.CreateScenarioCmd
    ReviseScenarioCmd = control_package.ReviseScenarioCmd

    create = CreateScenarioCmd(description="Design a schema repair scenario.")
    confirm = ConfirmScenarioCmd()
    revise = ReviseScenarioCmd(feedback="Make the failure mode more concrete.")
    cancel = CancelScenarioCmd()

    assert create.type == "create_scenario"
    assert create.description == "Design a schema repair scenario."
    assert confirm.type == "confirm_scenario"
    assert revise.type == "revise_scenario"
    assert revise.feedback == "Make the failure mode more concrete."
    assert cancel.type == "cancel_scenario"


def test_python_control_reexports_run_setup_commands() -> None:
    ListScenariosCmd = control_package.ListScenariosCmd
    StartRunCmd = control_package.StartRunCmd

    list_scenarios = ListScenariosCmd()
    start_run = StartRunCmd(scenario="schema_repair", generations=3)

    assert list_scenarios.type == "list_scenarios"
    assert start_run.type == "start_run"
    assert start_run.scenario == "schema_repair"
    assert start_run.generations == 3


def test_python_control_requires_stage_for_scenario_error_messages() -> None:
    ScenarioErrorMsg = control_package.ScenarioErrorMsg

    try:
        ScenarioErrorMsg(message="designer failed")
    except ValidationError:
        pass
    else:
        raise AssertionError("ScenarioErrorMsg should require stage")


def test_python_control_reexports_scenario_generation_lifecycle_messages() -> None:
    ScenarioErrorMsg = control_package.ScenarioErrorMsg
    ScenarioGeneratingMsg = control_package.ScenarioGeneratingMsg
    ScenarioPreviewMsg = control_package.ScenarioPreviewMsg
    ScenarioReadyMsg = control_package.ScenarioReadyMsg
    ScoringComponent = control_package.ScoringComponent
    StrategyParam = control_package.StrategyParam

    generating = ScenarioGeneratingMsg(name="schema_repair")
    preview = ScenarioPreviewMsg(
        name="schema_repair",
        display_name="Schema Repair",
        description="Recover a schema from examples.",
        strategy_params=[
            StrategyParam(name="depth", description="Reasoning depth"),
        ],
        scoring_components=[
            ScoringComponent(name="accuracy", description="Schema fidelity", weight=0.8),
        ],
        constraints=["No external tools"],
        win_threshold=0.75,
    )
    ready = ScenarioReadyMsg(name="schema_repair", test_scores=[0.8, 0.9])
    error = ScenarioErrorMsg(message="designer failed", stage="preview")

    assert generating.type == "scenario_generating"
    assert generating.name == "schema_repair"
    assert preview.type == "scenario_preview"
    assert preview.strategy_params[0].name == "depth"
    assert preview.scoring_components[0].weight == 0.8
    assert preview.constraints == ["No external tools"]
    assert preview.win_threshold == 0.75
    assert ready.type == "scenario_ready"
    assert ready.test_scores == [0.8, 0.9]
    assert error.type == "scenario_error"
    assert error.stage == "preview"


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
