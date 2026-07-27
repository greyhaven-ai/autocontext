/**
 * WebSocket protocol types — Zod schemas for client↔server messages (AC-347 Task 24).
 * Mirrors Python's autocontext/server/protocol.py.
 */

import { z } from "zod";

import { AGENT_TASK_PLAN_CAPABILITY } from "../loop/agent-task-plan.js";

export {
  AGENT_TASK_PLAN_CAPABILITY,
  AGENT_TASK_PLAN_EVENT_NAME,
  AgentTaskPlanIdSchema,
  AgentTaskPlanPayloadSchema,
  AgentTaskPlanStepSchema,
  AgentTaskPlanStepStatusSchema,
  MAX_RETAINED_AGENT_TASK_PLAN_BYTES,
} from "../loop/agent-task-plan.js";
export type {
  AgentTaskPlanPayload,
  AgentTaskPlanStep,
  AgentTaskPlanStepStatus,
} from "../loop/agent-task-plan.js";

export const PROTOCOL_VERSION = 1;
export const TRANSCRIPT_PROTOCOL_VERSION = 1;
export const TRANSCRIPT_PROTOCOL_QUERY_PARAM = "transcript_protocol_version";
export const TRANSCRIPT_PROTOCOL_QUERY_VALUE = String(TRANSCRIPT_PROTOCOL_VERSION);
export const SERVER_CAPABILITIES = [
  "run_transcript_v1",
  "safe_run_stop_v1",
  AGENT_TASK_PLAN_CAPABILITY,
] as const;

const protocolObject = <T extends z.ZodRawShape>(shape: T) => z.object(shape).strict();

export const PYTHON_SHARED_SERVER_MESSAGE_TYPES = [
  "hello",
  "event",
  "state",
  "chat_response",
  "environments",
  "run_accepted",
  "ack",
  "error",
  "scenario_generating",
  "scenario_preview",
  "scenario_ready",
  "scenario_error",
  "monitor_alert",
] as const;

export const TYPESCRIPT_ONLY_SERVER_MESSAGE_TYPES = ["auth_status", "mission_progress"] as const;

export const SERVER_MESSAGE_TYPES = [
  ...PYTHON_SHARED_SERVER_MESSAGE_TYPES,
  ...TYPESCRIPT_ONLY_SERVER_MESSAGE_TYPES,
] as const;

export const PYTHON_SHARED_CLIENT_MESSAGE_TYPES = [
  "pause",
  "resume",
  "stop",
  "inject_hint",
  "override_gate",
  "chat_agent",
  "start_run",
  "list_scenarios",
  "create_scenario",
  "confirm_scenario",
  "revise_scenario",
  "cancel_scenario",
] as const;

export const TYPESCRIPT_ONLY_CLIENT_MESSAGE_TYPES = [
  "resume_run",
  "login",
  "logout",
  "switch_provider",
  "whoami",
] as const;

export const CLIENT_MESSAGE_TYPES = [
  ...PYTHON_SHARED_CLIENT_MESSAGE_TYPES,
  ...TYPESCRIPT_ONLY_CLIENT_MESSAGE_TYPES,
] as const;

// ---------------------------------------------------------------------------
// Nested models
// ---------------------------------------------------------------------------

export const ScenarioInfoSchema = protocolObject({
  name: z.string(),
  description: z.string(),
});

export const ExecutorResourcesSchema = protocolObject({
  docker_image: z.string(),
  cpu_cores: z.number().int(),
  memory_gb: z.number().int(),
  disk_gb: z.number().int(),
  timeout_minutes: z.number().int(),
});

export const ExecutorInfoSchema = protocolObject({
  mode: z.string(),
  available: z.boolean(),
  description: z.string(),
  resources: ExecutorResourcesSchema.optional().nullable(),
});

export const StrategyParamSchema = protocolObject({
  name: z.string(),
  description: z.string(),
});

export const ScoringComponentSchema = protocolObject({
  name: z.string(),
  description: z.string(),
  weight: z.number(),
});

const RunMessageMetadataSchema = {
  client_run_id: z.string().min(1).max(200).optional(),
  event_id: z.string().min(1).optional(),
  sequence: z.number().int().nonnegative().optional(),
  run_id: z.string().min(1).optional(),
  occurred_at: z.union([z.string(), z.number()]).optional(),
} as const;

const RunCommandMetadataSchema = {
  client_run_id: z.string().min(1).max(200).optional(),
  command_id: z.string().min(1).max(200).optional(),
} as const;

// ---------------------------------------------------------------------------
// Server → Client messages
// ---------------------------------------------------------------------------

export const HelloMsgSchema = protocolObject({
  type: z.literal("hello"),
  protocol_version: z.number().int().optional(),
  transcript_protocol_version: z.number().int().positive().optional(),
  capabilities: z.array(z.string()).optional(),
});

export const EventMsgSchema = protocolObject({
  type: z.literal("event"),
  event: z.string(),
  payload: z.record(z.unknown()),
  ...RunMessageMetadataSchema,
});

export const StateMsgSchema = protocolObject({
  type: z.literal("state"),
  paused: z.boolean(),
  generation: z.number().int().optional(),
  phase: z.string().optional(),
  ...RunMessageMetadataSchema,
});

export const ChatResponseMsgSchema = protocolObject({
  type: z.literal("chat_response"),
  role: z.string(),
  text: z.string(),
  command_id: z.string().min(1).max(200).optional(),
  ...RunMessageMetadataSchema,
});

export const EnvironmentsMsgSchema = protocolObject({
  type: z.literal("environments"),
  scenarios: z.array(ScenarioInfoSchema),
  executors: z.array(ExecutorInfoSchema),
  current_executor: z.string(),
  agent_provider: z.string(),
});

export const RunAcceptedMsgSchema = protocolObject({
  type: z.literal("run_accepted"),
  ...RunMessageMetadataSchema,
  run_id: z.string(),
  scenario: z.string(),
  generations: z.number().int(),
  command_id: z.string().min(1).max(200).optional(),
});

export const AckMsgSchema = protocolObject({
  type: z.literal("ack"),
  action: z.string(),
  decision: z.string().optional().nullable(),
  command_id: z.string().min(1).max(200).optional(),
  ...RunMessageMetadataSchema,
});

export const ErrorMsgSchema = protocolObject({
  type: z.literal("error"),
  message: z.string(),
  command_id: z.string().min(1).max(200).optional(),
  ...RunMessageMetadataSchema,
});

export const ScenarioGeneratingMsgSchema = protocolObject({
  type: z.literal("scenario_generating"),
  name: z.string(),
});

export const ScenarioPreviewMsgSchema = protocolObject({
  type: z.literal("scenario_preview"),
  name: z.string(),
  display_name: z.string(),
  description: z.string(),
  strategy_params: z.array(StrategyParamSchema),
  scoring_components: z.array(ScoringComponentSchema),
  constraints: z.array(z.string()),
  win_threshold: z.number(),
});

export const ScenarioReadyMsgSchema = protocolObject({
  type: z.literal("scenario_ready"),
  name: z.string(),
  test_scores: z.array(z.number()),
});

export const ScenarioErrorMsgSchema = protocolObject({
  type: z.literal("scenario_error"),
  message: z.string(),
  stage: z.string(),
});

export const MonitorAlertMsgSchema = protocolObject({
  type: z.literal("monitor_alert"),
  alert_id: z.string(),
  condition_id: z.string(),
  condition_name: z.string(),
  condition_type: z.string(),
  scope: z.string(),
  detail: z.string(),
  ...RunMessageMetadataSchema,
});

// Mission progress (AC-414)
export const MissionProgressMsgSchema = protocolObject({
  type: z.literal("mission_progress"),
  missionId: z.string(),
  status: z.string(),
  stepsCompleted: z.number(),
  latestStep: z.string().optional(),
  budgetUsed: z.number().optional(),
  budgetMax: z.number().optional(),
});

// Auth status response (AC-408)
export const AuthStatusMsgSchema = protocolObject({
  type: z.literal("auth_status"),
  provider: z.string(),
  authenticated: z.boolean(),
  model: z.string().optional(),
  configuredProviders: z
    .array(
      protocolObject({
        provider: z.string(),
        hasApiKey: z.boolean(),
      }),
    )
    .optional(),
});

// ---------------------------------------------------------------------------
// Client → Server commands
// ---------------------------------------------------------------------------

export const PauseCmdSchema = protocolObject({
  type: z.literal("pause"),
  ...RunCommandMetadataSchema,
});
export const ResumeCmdSchema = protocolObject({
  type: z.literal("resume"),
  ...RunCommandMetadataSchema,
});

export const StopCmdSchema = protocolObject({
  type: z.literal("stop"),
  client_run_id: z.string().min(1).max(200),
  command_id: z.string().min(1).max(200),
});

export const InjectHintCmdSchema = protocolObject({
  type: z.literal("inject_hint"),
  text: z.string().min(1),
  ...RunCommandMetadataSchema,
});

export const OverrideGateCmdSchema = protocolObject({
  type: z.literal("override_gate"),
  decision: z.enum(["advance", "retry", "rollback"]),
  ...RunCommandMetadataSchema,
});

export const ChatAgentCmdSchema = protocolObject({
  type: z.literal("chat_agent"),
  role: z.string(),
  message: z.string().min(1),
  ...RunCommandMetadataSchema,
});

export const StartRunCmdSchema = protocolObject({
  type: z.literal("start_run"),
  scenario: z.string(),
  generations: z.number().int().positive(),
  require_playbook_approval: z.boolean().default(false),
  ...RunCommandMetadataSchema,
});

export const ResumeRunCmdSchema = protocolObject({
  type: z.literal("resume_run"),
  client_run_id: z.string().min(1).max(200),
  after_sequence: z.number().int().nonnegative(),
  command_id: z.string().min(1).max(200).optional(),
});

export const ListScenariosCmdSchema = protocolObject({
  type: z.literal("list_scenarios"),
});

export const CreateScenarioCmdSchema = protocolObject({
  type: z.literal("create_scenario"),
  description: z.string().min(1),
});

export const ConfirmScenarioCmdSchema = protocolObject({
  type: z.literal("confirm_scenario"),
});

export const ReviseScenarioCmdSchema = protocolObject({
  type: z.literal("revise_scenario"),
  feedback: z.string().min(1),
});

export const CancelScenarioCmdSchema = protocolObject({
  type: z.literal("cancel_scenario"),
});

// Auth commands (AC-408)
export const LoginCmdSchema = protocolObject({
  type: z.literal("login"),
  provider: z.string().min(1),
  apiKey: z.string().optional(),
  model: z.string().optional(),
  baseUrl: z.string().optional(),
});

export const LogoutCmdSchema = protocolObject({
  type: z.literal("logout"),
  provider: z.string().optional(),
});

export const SwitchProviderCmdSchema = protocolObject({
  type: z.literal("switch_provider"),
  provider: z.string().min(1),
});

export const WhoamiCmdSchema = protocolObject({
  type: z.literal("whoami"),
});

// ---------------------------------------------------------------------------
// Discriminated unions
// ---------------------------------------------------------------------------

export const ServerMessageSchema = z.discriminatedUnion("type", [
  HelloMsgSchema,
  EventMsgSchema,
  StateMsgSchema,
  ChatResponseMsgSchema,
  EnvironmentsMsgSchema,
  RunAcceptedMsgSchema,
  AckMsgSchema,
  ErrorMsgSchema,
  ScenarioGeneratingMsgSchema,
  ScenarioPreviewMsgSchema,
  ScenarioReadyMsgSchema,
  ScenarioErrorMsgSchema,
  MonitorAlertMsgSchema,
  MissionProgressMsgSchema,
  AuthStatusMsgSchema,
]);

export const ClientMessageSchema = z.discriminatedUnion("type", [
  PauseCmdSchema,
  ResumeCmdSchema,
  StopCmdSchema,
  InjectHintCmdSchema,
  OverrideGateCmdSchema,
  ChatAgentCmdSchema,
  StartRunCmdSchema,
  ListScenariosCmdSchema,
  CreateScenarioCmdSchema,
  ConfirmScenarioCmdSchema,
  ReviseScenarioCmdSchema,
  CancelScenarioCmdSchema,
  ResumeRunCmdSchema,
  LoginCmdSchema,
  LogoutCmdSchema,
  SwitchProviderCmdSchema,
  WhoamiCmdSchema,
]);

export type ServerMessage = z.infer<typeof ServerMessageSchema>;
export type ClientMessage = z.infer<typeof ClientMessageSchema>;

export function parseClientMessage(raw: Record<string, unknown>): ClientMessage {
  return ClientMessageSchema.parse(raw);
}

export function parseServerMessage(raw: Record<string, unknown>): ServerMessage {
  return ServerMessageSchema.parse(raw);
}
