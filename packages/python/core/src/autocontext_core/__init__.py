"""Facade for the future autocontext Apache core artifact."""

from importlib import import_module
from typing import Any

_elo = import_module("autocontext.harness.scoring.elo")
_context_budget = import_module("autocontext.prompts.context_budget")
_templates = import_module("autocontext.prompts.templates")
_providers_base = import_module("autocontext.providers.base")
_execution_judge = import_module("autocontext.execution.judge")
_rubric_coherence = import_module("autocontext.execution.rubric_coherence")
_scenarios_agent_task = import_module("autocontext.scenarios.agent_task")
_scenarios_artifact_editing = import_module("autocontext.scenarios.artifact_editing")
_scenarios_base = import_module("autocontext.scenarios.base")
_scenarios_coordination = import_module("autocontext.scenarios.coordination")
_scenarios_investigation = import_module("autocontext.scenarios.investigation")
_scenarios_negotiation = import_module("autocontext.scenarios.negotiation")
_scenarios_operator_loop = import_module("autocontext.scenarios.operator_loop")
_scenarios_schema_evolution = import_module("autocontext.scenarios.schema_evolution")
_scenarios_simulation = import_module("autocontext.scenarios.simulation")
_scenarios_tool_fragility = import_module("autocontext.scenarios.tool_fragility")
_scenarios_workflow = import_module("autocontext.scenarios.workflow")
_storage_row_types = import_module("autocontext.storage.row_types")

expected_score = _elo.expected_score
update_elo = _elo.update_elo
ContextBudget = _context_budget.ContextBudget
ContextBudgetPolicy = _context_budget.ContextBudgetPolicy
ContextBudgetResult = _context_budget.ContextBudgetResult
ContextBudgetTelemetry = _context_budget.ContextBudgetTelemetry
estimate_tokens = _context_budget.estimate_tokens
PromptBundle = _templates.PromptBundle
build_prompt_bundle = _templates.build_prompt_bundle
Observation: Any = _scenarios_base.Observation
Result: Any = _scenarios_base.Result
ReplayEnvelope: Any = _scenarios_base.ReplayEnvelope
GenerationMetrics: Any = _scenarios_base.GenerationMetrics
ExecutionLimits: Any = _scenarios_base.ExecutionLimits
ScenarioInterface: Any = _scenarios_base.ScenarioInterface
AgentTaskResult: Any = _scenarios_agent_task.AgentTaskResult
AgentTaskInterface: Any = _scenarios_agent_task.AgentTaskInterface
Artifact: Any = _scenarios_artifact_editing.Artifact
ArtifactDiff: Any = _scenarios_artifact_editing.ArtifactDiff
ArtifactValidationResult: Any = _scenarios_artifact_editing.ArtifactValidationResult
ArtifactEditingResult: Any = _scenarios_artifact_editing.ArtifactEditingResult
ArtifactEditingInterface: Any = _scenarios_artifact_editing.ArtifactEditingInterface
ActionSpec: Any = _scenarios_simulation.ActionSpec
Action: Any = _scenarios_simulation.Action
ActionResult: Any = _scenarios_simulation.ActionResult
ActionRecord: Any = _scenarios_simulation.ActionRecord
ActionTrace: Any = _scenarios_simulation.ActionTrace
EnvironmentSpec: Any = _scenarios_simulation.EnvironmentSpec
SimulationResult: Any = _scenarios_simulation.SimulationResult
SimulationInterface: Any = _scenarios_simulation.SimulationInterface
HiddenPreferences: Any = _scenarios_negotiation.HiddenPreferences
NegotiationRound: Any = _scenarios_negotiation.NegotiationRound
OpponentModel: Any = _scenarios_negotiation.OpponentModel
NegotiationResult: Any = _scenarios_negotiation.NegotiationResult
NegotiationInterface: Any = _scenarios_negotiation.NegotiationInterface
EvidenceItem: Any = _scenarios_investigation.EvidenceItem
EvidenceChain: Any = _scenarios_investigation.EvidenceChain
InvestigationResult: Any = _scenarios_investigation.InvestigationResult
InvestigationInterface: Any = _scenarios_investigation.InvestigationInterface
WorkflowStep: Any = _scenarios_workflow.WorkflowStep
SideEffect: Any = _scenarios_workflow.SideEffect
CompensationAction: Any = _scenarios_workflow.CompensationAction
WorkflowResult: Any = _scenarios_workflow.WorkflowResult
WorkflowInterface: Any = _scenarios_workflow.WorkflowInterface
SchemaMutation: Any = _scenarios_schema_evolution.SchemaMutation
ContextValidity: Any = _scenarios_schema_evolution.ContextValidity
SchemaEvolutionResult: Any = _scenarios_schema_evolution.SchemaEvolutionResult
SchemaEvolutionInterface: Any = _scenarios_schema_evolution.SchemaEvolutionInterface
ToolContract: Any = _scenarios_tool_fragility.ToolContract
ToolDrift: Any = _scenarios_tool_fragility.ToolDrift
FailureAttribution: Any = _scenarios_tool_fragility.FailureAttribution
ToolFragilityResult: Any = _scenarios_tool_fragility.ToolFragilityResult
ToolFragilityInterface: Any = _scenarios_tool_fragility.ToolFragilityInterface
ClarificationRequest: Any = _scenarios_operator_loop.ClarificationRequest
EscalationEvent: Any = _scenarios_operator_loop.EscalationEvent
OperatorLoopResult: Any = _scenarios_operator_loop.OperatorLoopResult
OperatorLoopInterface: Any = _scenarios_operator_loop.OperatorLoopInterface
WorkerContext: Any = _scenarios_coordination.WorkerContext
HandoffRecord: Any = _scenarios_coordination.HandoffRecord
CoordinationResult: Any = _scenarios_coordination.CoordinationResult
CoordinationInterface: Any = _scenarios_coordination.CoordinationInterface
CompletionResult: Any = _providers_base.CompletionResult
LLMProvider: Any = _providers_base.LLMProvider
ProviderError: Any = _providers_base.ProviderError
ParseMethod: Any = _execution_judge.ParseMethod
DisagreementMetrics: Any = _execution_judge.DisagreementMetrics
JudgeResult: Any = _execution_judge.JudgeResult
RubricCoherenceResult: Any = _rubric_coherence.RubricCoherenceResult
check_rubric_coherence = _rubric_coherence.check_rubric_coherence
RunRow: Any = _storage_row_types.RunRow
GenerationMetricsRow: Any = _storage_row_types.GenerationMetricsRow
MatchRow: Any = _storage_row_types.MatchRow
KnowledgeSnapshotRow: Any = _storage_row_types.KnowledgeSnapshotRow
AgentOutputRow: Any = _storage_row_types.AgentOutputRow
HumanFeedbackRow: Any = _storage_row_types.HumanFeedbackRow
TaskQueueRow: Any = _storage_row_types.TaskQueueRow

PACKAGE_ROLE = "core"
PACKAGE_TOPOLOGY_VERSION = 1

package_role = PACKAGE_ROLE
package_topology_version = PACKAGE_TOPOLOGY_VERSION

__all__ = [
    "Action",
    "ActionRecord",
    "ActionResult",
    "ActionSpec",
    "ActionTrace",
    "AgentOutputRow",
    "AgentTaskInterface",
    "AgentTaskResult",
    "Artifact",
    "ArtifactDiff",
    "ArtifactEditingInterface",
    "ArtifactEditingResult",
    "ArtifactValidationResult",
    "ClarificationRequest",
    "CompensationAction",
    "CompletionResult",
    "ContextValidity",
    "ContextBudget",
    "ContextBudgetPolicy",
    "ContextBudgetResult",
    "ContextBudgetTelemetry",
    "DisagreementMetrics",
    "EnvironmentSpec",
    "CoordinationInterface",
    "CoordinationResult",
    "ExecutionLimits",
    "FailureAttribution",
    "GenerationMetrics",
    "GenerationMetricsRow",
    "HandoffRecord",
    "HumanFeedbackRow",
    "InvestigationInterface",
    "InvestigationResult",
    "JudgeResult",
    "KnowledgeSnapshotRow",
    "LLMProvider",
    "MatchRow",
    "NegotiationInterface",
    "NegotiationResult",
    "NegotiationRound",
    "Observation",
    "OperatorLoopInterface",
    "OperatorLoopResult",
    "OpponentModel",
    "PACKAGE_ROLE",
    "PACKAGE_TOPOLOGY_VERSION",
    "ParseMethod",
    "PromptBundle",
    "ProviderError",
    "ReplayEnvelope",
    "Result",
    "RubricCoherenceResult",
    "RunRow",
    "ScenarioInterface",
    "SchemaEvolutionInterface",
    "SchemaEvolutionResult",
    "SchemaMutation",
    "SimulationInterface",
    "SimulationResult",
    "EscalationEvent",
    "EvidenceChain",
    "EvidenceItem",
    "HiddenPreferences",
    "SideEffect",
    "TaskQueueRow",
    "ToolContract",
    "ToolDrift",
    "ToolFragilityInterface",
    "ToolFragilityResult",
    "WorkerContext",
    "WorkflowInterface",
    "WorkflowResult",
    "WorkflowStep",
    "build_prompt_bundle",
    "check_rubric_coherence",
    "estimate_tokens",
    "expected_score",
    "package_role",
    "package_topology_version",
    "update_elo",
]
