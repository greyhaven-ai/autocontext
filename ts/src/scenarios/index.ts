export type { AgentTaskSpec } from "./agent-task-spec.js";
export { AgentTaskSpecSchema, parseRawSpec } from "./agent-task-spec.js";
export type { ArtifactEditingSpec, ArtifactSpec } from "./artifact-editing-spec.js";
export { ArtifactEditingSpecSchema, ArtifactSpecSchema, parseRawArtifactEditingSpec } from "./artifact-editing-spec.js";
export {
  ARTIFACT_SPEC_START,
  ARTIFACT_SPEC_END,
  ARTIFACT_EDITING_DESIGNER_SYSTEM,
  parseArtifactEditingSpec,
  designArtifactEditing,
} from "./artifact-editing-designer.js";
export { ArtifactEditingCreator } from "./artifact-editing-creator.js";
export type { ArtifactEditingCreatorOpts, ArtifactEditingScenarioHandle } from "./artifact-editing-creator.js";
export {
  INVESTIGATION_SPEC_START,
  INVESTIGATION_SPEC_END,
  INVESTIGATION_DESIGNER_SYSTEM,
  parseInvestigationSpec,
  designInvestigation,
} from "./investigation-designer.js";
export { InvestigationCreator } from "./investigation-creator.js";
export type { InvestigationCreatorOpts, InvestigationScenarioHandle } from "./investigation-creator.js";
export type { InvestigationSpec } from "./investigation-spec.js";
export { InvestigationSpecSchema, parseRawInvestigationSpec } from "./investigation-spec.js";
export { parseAgentTaskSpec, designAgentTask, SPEC_START, SPEC_END, AGENT_TASK_DESIGNER_SYSTEM } from "./agent-task-designer.js";
export { validateSpec } from "./agent-task-validator.js";
export { createAgentTask } from "./agent-task-factory.js";
export type { AgentTaskFactoryOpts } from "./agent-task-factory.js";
export { AgentTaskCreator } from "./agent-task-creator.js";
export type { AgentTaskCreatorOpts, CreatedScenario } from "./agent-task-creator.js";
export { classifyScenarioFamily, routeToFamily, LowConfidenceError } from "./family-classifier.js";
export type { FamilyCandidate, FamilyClassification } from "./family-classifier.js";
export { getPipeline, hasPipeline, UnsupportedFamilyError, validateForFamily } from "./family-pipeline.js";
export type { FamilyPipeline } from "./family-pipeline.js";
export {
  SIM_SPEC_START,
  SIM_SPEC_END,
  SIMULATION_DESIGNER_SYSTEM,
  parseSimulationSpec,
  designSimulation,
} from "./simulation-designer.js";
export { SimulationCreator, shouldUseSimulationFamily } from "./simulation-creator.js";
export type { SimulationCreatorOpts, SimulationScenarioHandle } from "./simulation-creator.js";
export type { SimulationSpec, SimulationActionSpec } from "./simulation-spec.js";
export { SimulationSpecSchema, SimulationActionSpecSchema, parseRawSimulationSpec } from "./simulation-spec.js";
export {
  WORKFLOW_SPEC_START,
  WORKFLOW_SPEC_END,
  WORKFLOW_DESIGNER_SYSTEM,
  parseWorkflowSpec,
  designWorkflow,
} from "./workflow-designer.js";
export { WorkflowCreator } from "./workflow-creator.js";
export type { WorkflowCreatorOpts, WorkflowScenarioHandle } from "./workflow-creator.js";
export type { WorkflowSpec, WorkflowStepSpec } from "./workflow-spec.js";
export { WorkflowSpecSchema, WorkflowStepSpecSchema, parseRawWorkflowSpec } from "./workflow-spec.js";
export { getScenarioTypeMarker, SCENARIO_TYPE_MARKERS } from "./families.js";

// Game scenario interface + Grid CTF (AC-343)
export type {
  ScenarioInterface,
  Observation,
  Result,
  ReplayEnvelope,
  ExecutionLimits,
  ScoringDimension,
  LegalAction,
} from "./game-interface.js";
export {
  ObservationSchema,
  ResultSchema,
  ReplayEnvelopeSchema,
  ExecutionLimitsSchema,
} from "./game-interface.js";
export { GridCtfScenario } from "./grid-ctf.js";
export { OthelloScenario } from "./othello.js";
export { ResourceTrader } from "./resource-trader.js";
export { WordCountTask } from "./word-count.js";
export { SCENARIO_REGISTRY, AGENT_TASK_REGISTRY, isGameScenario, isAgentTask } from "./registry.js";
export type { BuiltinAgentTask } from "./registry.js";

// Custom scenario pipeline (AC-348)
export {
  loadCustomScenarios,
  registerCustomScenarios,
  discoverAndRegisterCustomScenarios,
  resolveCustomAgentTask,
  renderAgentTaskPrompt,
} from "./custom-loader.js";
export type { CustomScenarioEntry, ResolvedCustomAgentTask } from "./custom-loader.js";
export { IntentValidator } from "./intent-validator.js";
export type { IntentValidationResult } from "./intent-validator.js";
export { createScenarioFromDescription } from "./scenario-creator.js";
export type { CreatedScenarioResult } from "./scenario-creator.js";

// Family interface contracts (AC-380)
export {
  isGameScenario as isGameFamily,
  isAgentTask as isAgentTaskFamily,
  isSimulation,
  isNegotiation,
  isInvestigation,
  isWorkflow,
  isSchemaEvolution,
  isToolFragility,
  isOperatorLoop,
  isCoordination,
  isArtifactEditing,
  assertFamilyContract,
  detectFamily,
} from "./family-interfaces.js";
export type {
  GameScenarioInterface,
  AgentTaskInterface as AgentTaskFamilyInterface,
  SimulationInterface,
  NegotiationInterface,
  InvestigationInterface,
  WorkflowInterface,
  SchemaEvolutionInterface,
  ToolFragilityInterface,
  OperatorLoopInterface,
  CoordinationInterface,
  ArtifactEditingInterface,
  ScenarioFamilyName as FamilyName,
} from "./family-interfaces.js";

// Codegen pipeline (AC-436)
export { generateScenarioSource, generateAndValidateScenarioSource, hasCodegen, ScenarioRuntime, CodegenUnsupportedFamilyError, validateGeneratedScenario } from "./codegen/index.js";
export type { ScenarioProxy, ScenarioRuntimeOpts, CodegenFn, ExecutionValidationResult } from "./codegen/index.js";
export { loadCustomScenario, readScenarioFamily } from "./codegen/loader.js";

// Spec auto-heal (AC-440)
export {
  needsSampleInput,
  generateSyntheticSampleInput,
  healAgentTaskSpec,
  healSpec,
  coerceSpecTypes,
  inferMissingFields,
} from "./spec-auto-heal.js";

// Scenario revision (AC-441)
export {
  buildRevisionPrompt,
  reviseSpec,
  reviseAgentTaskOutput,
} from "./scenario-revision.js";
export type { RevisionResult, JudgeResult, RevisionPromptOpts, ReviseSpecOpts, OutputRevisionOpts } from "./scenario-revision.js";

// Scenario templates (AC-443)
export { TemplateLoader } from "./templates/index.js";
export type { TemplateSpec, RubricDimension } from "./templates/index.js";

// Scenario materialization (AC-433)
export { materializeScenario } from "./materialize.js";
export type { MaterializeOpts, MaterializeResult } from "./materialize.js";
