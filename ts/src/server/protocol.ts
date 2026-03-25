/**
 * WebSocket protocol types — Zod schemas for client↔server messages (AC-347 Task 24).
 * Mirrors Python's autocontext/server/protocol.py.
 */

import { z } from "zod";

export const PROTOCOL_VERSION = 1;

// ---------------------------------------------------------------------------
// Nested models
// ---------------------------------------------------------------------------

export const ScenarioInfoSchema = z.object({
  name: z.string(),
  description: z.string(),
});

export const ExecutorResourcesSchema = z.object({
  docker_image: z.string(),
  cpu_cores: z.number(),
  memory_gb: z.number(),
  disk_gb: z.number(),
  timeout_minutes: z.number().int(),
});

export const ExecutorInfoSchema = z.object({
  mode: z.string(),
  available: z.boolean(),
  description: z.string(),
  resources: ExecutorResourcesSchema.optional().nullable(),
});

export const StrategyParamSchema = z.object({
  name: z.string(),
  description: z.string(),
});

export const ScoringComponentSchema = z.object({
  name: z.string(),
  description: z.string(),
  weight: z.number(),
});

// ---------------------------------------------------------------------------
// Server → Client messages
// ---------------------------------------------------------------------------

export const HelloMsgSchema = z.object({
  type: z.literal("hello"),
  protocol_version: z.number().int().optional(),
});

export const EventMsgSchema = z.object({
  type: z.literal("event"),
  event: z.string(),
  payload: z.record(z.unknown()),
});

export const StateMsgSchema = z.object({
  type: z.literal("state"),
  paused: z.boolean(),
  generation: z.number().int().optional(),
  phase: z.string().optional(),
});

export const ChatResponseMsgSchema = z.object({
  type: z.literal("chat_response"),
  role: z.string(),
  text: z.string(),
});

export const EnvironmentsMsgSchema = z.object({
  type: z.literal("environments"),
  scenarios: z.array(ScenarioInfoSchema),
  executors: z.array(ExecutorInfoSchema),
  current_executor: z.string(),
  agent_provider: z.string(),
});

export const RunAcceptedMsgSchema = z.object({
  type: z.literal("run_accepted"),
  run_id: z.string(),
  scenario: z.string(),
  generations: z.number().int(),
});

export const AckMsgSchema = z.object({
  type: z.literal("ack"),
  action: z.string(),
  decision: z.string().optional(),
});

export const ErrorMsgSchema = z.object({
  type: z.literal("error"),
  message: z.string(),
});

export const ScenarioGeneratingMsgSchema = z.object({
  type: z.literal("scenario_generating"),
  name: z.string(),
});

export const ScenarioPreviewMsgSchema = z.object({
  type: z.literal("scenario_preview"),
  name: z.string(),
  display_name: z.string(),
  description: z.string(),
  strategy_params: z.array(StrategyParamSchema),
  scoring_components: z.array(ScoringComponentSchema),
  constraints: z.array(z.string()),
  win_threshold: z.number(),
});

export const ScenarioReadyMsgSchema = z.object({
  type: z.literal("scenario_ready"),
  name: z.string(),
  test_scores: z.array(z.number()),
});

export const ScenarioErrorMsgSchema = z.object({
  type: z.literal("scenario_error"),
  message: z.string(),
  stage: z.string().optional(),
});

export const MonitorAlertMsgSchema = z.object({
  type: z.literal("monitor_alert"),
  alert_id: z.string(),
  condition_id: z.string(),
  condition_name: z.string(),
  condition_type: z.string(),
  scope: z.string(),
  detail: z.record(z.unknown()),
});

// Auth status response (AC-408)
export const AuthStatusMsgSchema = z.object({
  type: z.literal("auth_status"),
  provider: z.string(),
  authenticated: z.boolean(),
  model: z.string().optional(),
  configuredProviders: z.array(z.object({
    provider: z.string(),
    hasApiKey: z.boolean(),
  })).optional(),
});

// ---------------------------------------------------------------------------
// Client → Server commands
// ---------------------------------------------------------------------------

export const PauseCmdSchema = z.object({ type: z.literal("pause") });
export const ResumeCmdSchema = z.object({ type: z.literal("resume") });

export const InjectHintCmdSchema = z.object({
  type: z.literal("inject_hint"),
  text: z.string().min(1),
});

export const OverrideGateCmdSchema = z.object({
  type: z.literal("override_gate"),
  decision: z.enum(["advance", "retry", "rollback"]),
});

export const ChatAgentCmdSchema = z.object({
  type: z.literal("chat_agent"),
  role: z.string(),
  message: z.string(),
});

export const StartRunCmdSchema = z.object({
  type: z.literal("start_run"),
  scenario: z.string(),
  generations: z.number().int().positive(),
});

export const ListScenariosCmdSchema = z.object({
  type: z.literal("list_scenarios"),
});

export const CreateScenarioCmdSchema = z.object({
  type: z.literal("create_scenario"),
  description: z.string().min(1),
});

export const ConfirmScenarioCmdSchema = z.object({
  type: z.literal("confirm_scenario"),
});

export const ReviseScenarioCmdSchema = z.object({
  type: z.literal("revise_scenario"),
  feedback: z.string().min(1),
});

export const CancelScenarioCmdSchema = z.object({
  type: z.literal("cancel_scenario"),
});

// Auth commands (AC-408)
export const LoginCmdSchema = z.object({
  type: z.literal("login"),
  provider: z.string().min(1),
  apiKey: z.string().optional(),
  model: z.string().optional(),
  baseUrl: z.string().optional(),
});

export const LogoutCmdSchema = z.object({
  type: z.literal("logout"),
  provider: z.string().optional(),
});

export const SwitchProviderCmdSchema = z.object({
  type: z.literal("switch_provider"),
  provider: z.string().min(1),
});

export const WhoamiCmdSchema = z.object({
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
  AuthStatusMsgSchema,
]);

export const ClientMessageSchema = z.discriminatedUnion("type", [
  PauseCmdSchema,
  ResumeCmdSchema,
  InjectHintCmdSchema,
  OverrideGateCmdSchema,
  ChatAgentCmdSchema,
  StartRunCmdSchema,
  ListScenariosCmdSchema,
  CreateScenarioCmdSchema,
  ConfirmScenarioCmdSchema,
  ReviseScenarioCmdSchema,
  CancelScenarioCmdSchema,
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
