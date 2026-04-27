"""Facade for the future autocontext control-plane artifact."""

from importlib import import_module
from typing import Any

_production_traces_contract = import_module(
    "autocontext.production_traces.contract.models"
)
_research_types = import_module("autocontext.research.types")
_server_protocol = import_module("autocontext.server.protocol")
_monitor_types = import_module("autocontext.monitor.types")
_agent_contracts = import_module("autocontext.agents.contracts")
_stagnation = import_module("autocontext.knowledge.stagnation")

PROTOCOL_VERSION = _server_protocol.PROTOCOL_VERSION
ScenarioInfo: Any = _server_protocol.ScenarioInfo
ExecutorResources: Any = _server_protocol.ExecutorResources
ExecutorInfo: Any = _server_protocol.ExecutorInfo
StrategyParam: Any = _server_protocol.StrategyParam
ScoringComponent: Any = _server_protocol.ScoringComponent
HelloMsg: Any = _server_protocol.HelloMsg
ChatResponseMsg: Any = _server_protocol.ChatResponseMsg
EventMsg: Any = _server_protocol.EventMsg
EnvironmentsMsg: Any = _server_protocol.EnvironmentsMsg
StateMsg: Any = _server_protocol.StateMsg
AckMsg: Any = _server_protocol.AckMsg
RunAcceptedMsg: Any = _server_protocol.RunAcceptedMsg
ErrorMsg: Any = _server_protocol.ErrorMsg
MonitorAlertMsg: Any = _server_protocol.MonitorAlertMsg
ConditionType: Any = _monitor_types.ConditionType
MonitorCondition: Any = _monitor_types.MonitorCondition
MonitorAlert: Any = _monitor_types.MonitorAlert
ScenarioGeneratingMsg: Any = _server_protocol.ScenarioGeneratingMsg
ScenarioPreviewMsg: Any = _server_protocol.ScenarioPreviewMsg
ScenarioReadyMsg: Any = _server_protocol.ScenarioReadyMsg
ScenarioErrorMsg: Any = _server_protocol.ScenarioErrorMsg
PauseCmd: Any = _server_protocol.PauseCmd
ResumeCmd: Any = _server_protocol.ResumeCmd
InjectHintCmd: Any = _server_protocol.InjectHintCmd
OverrideGateCmd: Any = _server_protocol.OverrideGateCmd
CreateScenarioCmd: Any = _server_protocol.CreateScenarioCmd
ConfirmScenarioCmd: Any = _server_protocol.ConfirmScenarioCmd
ReviseScenarioCmd: Any = _server_protocol.ReviseScenarioCmd
CancelScenarioCmd: Any = _server_protocol.CancelScenarioCmd
Urgency: Any = _research_types.Urgency
CompetitorOutput: Any = _agent_contracts.CompetitorOutput
AnalystOutput: Any = _agent_contracts.AnalystOutput
CoachOutput: Any = _agent_contracts.CoachOutput
ArchitectOutput: Any = _agent_contracts.ArchitectOutput
StagnationReport: Any = _stagnation.StagnationReport
ResearchQuery: Any = _research_types.ResearchQuery
Citation: Any = _research_types.Citation
ResearchResult: Any = _research_types.ResearchResult
ResearchAdapter: Any = _research_types.ResearchAdapter
ResearchConfig: Any = _research_types.ResearchConfig
Sdk: Any = _production_traces_contract.Sdk
TraceSource: Any = _production_traces_contract.TraceSource
Provider: Any = _production_traces_contract.Provider
EnvContext: Any = _production_traces_contract.EnvContext
ToolCall: Any = _production_traces_contract.ToolCall
Error: Any = _production_traces_contract.Error
ProductionOutcome: Any = _production_traces_contract.ProductionOutcome
UsageInfo: Any = _production_traces_contract.UsageInfo
EvalExampleId: Any = _production_traces_contract.EvalExampleId
TrainingRecordId: Any = _production_traces_contract.TrainingRecordId
TraceLinks: Any = _production_traces_contract.TraceLinks
Chosen: Any = _production_traces_contract.Chosen
Routing: Any = _production_traces_contract.Routing
UserIdHash: Any = _production_traces_contract.UserIdHash
EndedAt: Any = _production_traces_contract.EndedAt
Items: Any = _production_traces_contract.Items
SessionIdentifier: Any = _production_traces_contract.SessionIdentifier
Message: Any = _production_traces_contract.Message
TimingInfo: Any = _production_traces_contract.TimingInfo
FeedbackRef: Any = _production_traces_contract.FeedbackRef
RedactionMarker: Any = _production_traces_contract.RedactionMarker
ProductionTrace: Any = _production_traces_contract.ProductionTrace

PACKAGE_ROLE = "control"
PACKAGE_TOPOLOGY_VERSION = 1

package_role = PACKAGE_ROLE
package_topology_version = PACKAGE_TOPOLOGY_VERSION

__all__ = [
    "AnalystOutput",
    "ArchitectOutput",
    "ChatResponseMsg",
    "Chosen",
    "Citation",
    "CancelScenarioCmd",
    "CoachOutput",
    "ConditionType",
    "ConfirmScenarioCmd",
    "CreateScenarioCmd",
    "EndedAt",
    "EnvContext",
    "EventMsg",
    "EnvironmentsMsg",
    "AckMsg",
    "Error",
    "ErrorMsg",
    "MonitorAlert",
    "MonitorAlertMsg",
    "MonitorCondition",
    "EvalExampleId",
    "FeedbackRef",
    "Items",
    "ExecutorInfo",
    "ExecutorResources",
    "HelloMsg",
    "InjectHintCmd",
    "Message",
    "PACKAGE_ROLE",
    "PACKAGE_TOPOLOGY_VERSION",
    "PROTOCOL_VERSION",
    "PauseCmd",
    "ProductionOutcome",
    "ProductionTrace",
    "Provider",
    "CompetitorOutput",
    "RunAcceptedMsg",
    "RedactionMarker",
    "ResearchAdapter",
    "ResearchConfig",
    "ResearchQuery",
    "ResearchResult",
    "ReviseScenarioCmd",
    "ResumeCmd",
    "Routing",
    "ScenarioErrorMsg",
    "ScenarioGeneratingMsg",
    "ScenarioInfo",
    "ScenarioPreviewMsg",
    "ScenarioReadyMsg",
    "OverrideGateCmd",
    "ScoringComponent",
    "Sdk",
    "SessionIdentifier",
    "StagnationReport",
    "StateMsg",
    "StrategyParam",
    "TimingInfo",
    "ToolCall",
    "TraceLinks",
    "TraceSource",
    "TrainingRecordId",
    "Urgency",
    "UsageInfo",
    "UserIdHash",
    "package_role",
    "package_topology_version",
]
