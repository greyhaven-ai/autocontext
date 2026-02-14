/** Agent role identifiers. */
export type Role = "competitor" | "analyst" | "coach" | "architect" | "curator";

/** Gate decision outcomes. */
export type GateDecision = "advance" | "retry" | "rollback";

/** Curator decision outcomes. */
export type CuratorDecision = "accept" | "reject" | "merge";

/** Per-role status in the agent panel. */
export type RoleStatus = "waiting" | "running" | "done" | "n/a";

/** Tracked state for a single agent role. */
export interface RoleState {
  status: RoleStatus;
  latencyMs: number | null;
  tokens: number | null;
}

/** A single row in the score trajectory table. */
export interface TrajectoryRow {
  generation: number;
  meanScore: number;
  bestScore: number;
  elo: number;
  gate: GateDecision;
}

/** Tournament progress state. */
export interface TournamentState {
  totalMatches: number;
  completedMatches: number;
  scores: number[];
  meanScore: number | null;
  bestScore: number | null;
  wins: number | null;
  losses: number | null;
}

/** A single chat message (from agent or user). */
export interface ChatMessage {
  sender: string;
  text: string;
}

/** Scenario creation phase. */
export type ScenarioCreationPhase = "idle" | "generating" | "preview" | "confirming" | "ready" | "error";

/** Preview of a generated scenario before confirmation. */
export interface ScenarioPreview {
  name: string;
  displayName: string;
  description: string;
  strategyParams: Array<{ name: string; description: string }>;
  scoringComponents: Array<{ name: string; description: string; weight: number }>;
  constraints: string[];
  winThreshold: number;
}

/** State for the scenario creation flow. */
export interface ScenarioCreationState {
  phase: ScenarioCreationPhase;
  name: string | null;
  preview: ScenarioPreview | null;
  errorMessage: string | null;
  testScores: number[] | null;
}

/** Complete TUI run state. */
export interface RunState {
  connected: boolean;
  runId: string | null;
  scenario: string | null;
  paused: boolean;
  totalGenerations: number | null;
  currentGeneration: number | null;
  generationStartedAt: number | null;
  phase: string | null;
  roles: Record<Role, RoleState>;
  trajectory: TrajectoryRow[];
  tournament: TournamentState;
  gateDecision: GateDecision | null;
  gateDelta: number | null;
  curatorDecision: CuratorDecision | null;
  chatMessages: ChatMessage[];
  chatTarget: Role;
  logLines: string[];
  scenarioCreation: ScenarioCreationState;
  scenarios: ScenarioInfo[];
  executors: ExecutorInfo[];
  currentExecutor: string | null;
  agentProvider: string | null;
}

// --- WebSocket message types ---

/** Server -> Client: protocol version handshake. */
export interface ServerHello {
  type: "hello";
  protocol_version: number;
}

/** Server -> Client event message. */
export interface ServerEventMessage {
  type: "event";
  event: string;
  payload: Record<string, unknown>;
}

/** Server -> Client state message. */
export interface ServerStateMessage {
  type: "state";
  paused: boolean;
  generation: number;
  phase: string;
}

/** Server -> Client chat response. */
export interface ServerChatResponse {
  type: "chat_response";
  role: string;
  text: string;
}

/** Scenario detail with description. */
export interface ScenarioInfo {
  name: string;
  description: string;
}

/** Executor environment detail. */
export interface ExecutorInfo {
  mode: string;
  available: boolean;
  description: string;
  resources?: {
    docker_image: string;
    cpu_cores: number;
    memory_gb: number;
    disk_gb: number;
    timeout_minutes: number;
  };
}

/** Server -> Client: available environments (scenarios + executors). */
export interface ServerEnvironmentsMessage {
  type: "environments";
  scenarios: ScenarioInfo[];
  executors: ExecutorInfo[];
  current_executor: string;
  agent_provider: string;
}

/** Server -> Client: run accepted. */
export interface ServerRunAccepted {
  type: "run_accepted";
  run_id: string;
  scenario: string;
  generations: number;
}

/** Server -> Client: acknowledgement. */
export interface ServerAck {
  type: "ack";
  action: string;
  decision?: string;
}

/** Server -> Client: scenario generation in progress. */
export interface ServerScenarioGenerating {
  type: "scenario_generating";
  name: string;
}

/** Server -> Client: scenario preview ready for confirmation. */
export interface ServerScenarioPreview {
  type: "scenario_preview";
  name: string;
  display_name: string;
  description: string;
  strategy_params: Array<{ name: string; description: string }>;
  scoring_components: Array<{ name: string; description: string; weight: number }>;
  constraints: string[];
  win_threshold: number;
}

/** Server -> Client: scenario ready for use. */
export interface ServerScenarioReady {
  type: "scenario_ready";
  name: string;
  test_scores: number[];
}

/** Server -> Client: scenario creation error. */
export interface ServerScenarioError {
  type: "scenario_error";
  message: string;
}

/** Server -> Client: error. */
export interface ServerError {
  type: "error";
  message: string;
}

export type ServerMessage =
  | ServerHello
  | ServerEventMessage
  | ServerStateMessage
  | ServerChatResponse
  | ServerEnvironmentsMessage
  | ServerRunAccepted
  | ServerAck
  | ServerError
  | ServerScenarioGenerating
  | ServerScenarioPreview
  | ServerScenarioReady
  | ServerScenarioError;

/** Client -> Server: pause. */
export interface ClientPause {
  type: "pause";
}

/** Client -> Server: resume. */
export interface ClientResume {
  type: "resume";
}

/** Client -> Server: inject hint. */
export interface ClientInjectHint {
  type: "inject_hint";
  text: string;
}

/** Client -> Server: override gate decision. */
export interface ClientOverrideGate {
  type: "override_gate";
  decision: GateDecision;
}

/** Client -> Server: chat with agent. */
export interface ClientChatAgent {
  type: "chat_agent";
  role: string;
  message: string;
}

/** Client -> Server: start a new run. */
export interface ClientStartRun {
  type: "start_run";
  scenario: string;
  generations: number;
}

/** Client -> Server: list available scenarios. */
export interface ClientListScenarios {
  type: "list_scenarios";
}

/** Client -> Server: create a custom scenario from description. */
export interface ClientCreateScenario {
  type: "create_scenario";
  description: string;
}

/** Client -> Server: confirm the previewed scenario. */
export interface ClientConfirmScenario {
  type: "confirm_scenario";
}

/** Client -> Server: revise the previewed scenario with feedback. */
export interface ClientReviseScenario {
  type: "revise_scenario";
  feedback: string;
}

/** Client -> Server: cancel scenario creation. */
export interface ClientCancelScenario {
  type: "cancel_scenario";
}

export type ClientMessage =
  | ClientPause
  | ClientResume
  | ClientInjectHint
  | ClientOverrideGate
  | ClientChatAgent
  | ClientStartRun
  | ClientListScenarios
  | ClientCreateScenario
  | ClientConfirmScenario
  | ClientReviseScenario
  | ClientCancelScenario;
