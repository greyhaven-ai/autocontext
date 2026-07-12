// AUTO-GENERATED from autocontext/src/autocontext/server/protocol.py
// Do not edit manually. Run: python scripts/generate_protocol.py
//
// Protocol version: 1

import { z } from "zod";

export const ExecutorResourcesSchema = z.object({
  docker_image: z.string(),
  cpu_cores: z.number().int(),
  memory_gb: z.number().int(),
  disk_gb: z.number().int(),
  timeout_minutes: z.number().int(),
}).strict();

export const ExecutorInfoSchema = z.object({
  mode: z.string(),
  available: z.boolean(),
  description: z.string(),
  resources: ExecutorResourcesSchema.optional().nullable(),
}).strict();

export const ScenarioInfoSchema = z.object({
  name: z.string(),
  description: z.string(),
}).strict();

export const ScoringComponentSchema = z.object({
  name: z.string(),
  description: z.string(),
  weight: z.number(),
}).strict();

export const StrategyParamSchema = z.object({
  name: z.string(),
  description: z.string(),
}).strict();

// --- Server -> Client messages ---

export const HelloMsgSchema = z.object({
  type: z.literal("hello"),
  protocol_version: z.number().int().optional(),
  transcript_protocol_version: z.number().int().optional().nullable(),
  capabilities: z.array(z.string()).optional().nullable(),
}).strict();

export const EventMsgSchema = z.object({
  client_run_id: z.string().optional().nullable(),
  event_id: z.string().optional().nullable(),
  sequence: z.number().int().optional().nullable(),
  run_id: z.string().optional().nullable(),
  occurred_at: z.union([z.string(), z.number()]).optional().nullable(),
  type: z.literal("event"),
  event: z.string(),
  payload: z.record(z.unknown()),
}).strict();

export const StateMsgSchema = z.object({
  client_run_id: z.string().optional().nullable(),
  event_id: z.string().optional().nullable(),
  sequence: z.number().int().optional().nullable(),
  run_id: z.string().optional().nullable(),
  occurred_at: z.union([z.string(), z.number()]).optional().nullable(),
  type: z.literal("state"),
  paused: z.boolean(),
  generation: z.number().int().optional(),
  phase: z.string().optional(),
}).strict();

export const ChatResponseMsgSchema = z.object({
  client_run_id: z.string().optional().nullable(),
  event_id: z.string().optional().nullable(),
  sequence: z.number().int().optional().nullable(),
  run_id: z.string().optional().nullable(),
  occurred_at: z.union([z.string(), z.number()]).optional().nullable(),
  type: z.literal("chat_response"),
  role: z.string(),
  text: z.string(),
  command_id: z.string().optional().nullable(),
}).strict();

export const EnvironmentsMsgSchema = z.object({
  type: z.literal("environments"),
  scenarios: z.array(ScenarioInfoSchema),
  executors: z.array(ExecutorInfoSchema),
  current_executor: z.string(),
  agent_provider: z.string(),
}).strict();

export const RunAcceptedMsgSchema = z.object({
  client_run_id: z.string().optional().nullable(),
  event_id: z.string().optional().nullable(),
  sequence: z.number().int().optional().nullable(),
  run_id: z.string(),
  occurred_at: z.union([z.string(), z.number()]).optional().nullable(),
  type: z.literal("run_accepted"),
  scenario: z.string(),
  generations: z.number().int(),
  command_id: z.string().optional().nullable(),
}).strict();

export const AckMsgSchema = z.object({
  client_run_id: z.string().optional().nullable(),
  event_id: z.string().optional().nullable(),
  sequence: z.number().int().optional().nullable(),
  run_id: z.string().optional().nullable(),
  occurred_at: z.union([z.string(), z.number()]).optional().nullable(),
  type: z.literal("ack"),
  action: z.string(),
  decision: z.string().optional().nullable(),
  command_id: z.string().optional().nullable(),
}).strict();

export const ErrorMsgSchema = z.object({
  client_run_id: z.string().optional().nullable(),
  event_id: z.string().optional().nullable(),
  sequence: z.number().int().optional().nullable(),
  run_id: z.string().optional().nullable(),
  occurred_at: z.union([z.string(), z.number()]).optional().nullable(),
  type: z.literal("error"),
  message: z.string(),
  command_id: z.string().optional().nullable(),
}).strict();

export const ScenarioGeneratingMsgSchema = z.object({
  type: z.literal("scenario_generating"),
  name: z.string(),
}).strict();

export const ScenarioPreviewMsgSchema = z.object({
  type: z.literal("scenario_preview"),
  name: z.string(),
  display_name: z.string(),
  description: z.string(),
  strategy_params: z.array(StrategyParamSchema),
  scoring_components: z.array(ScoringComponentSchema),
  constraints: z.array(z.string()),
  win_threshold: z.number(),
}).strict();

export const ScenarioReadyMsgSchema = z.object({
  type: z.literal("scenario_ready"),
  name: z.string(),
  test_scores: z.array(z.number()),
}).strict();

export const ScenarioErrorMsgSchema = z.object({
  type: z.literal("scenario_error"),
  message: z.string(),
  stage: z.string(),
}).strict();

export const MonitorAlertMsgSchema = z.object({
  client_run_id: z.string().optional().nullable(),
  event_id: z.string().optional().nullable(),
  sequence: z.number().int().optional().nullable(),
  run_id: z.string().optional().nullable(),
  occurred_at: z.union([z.string(), z.number()]).optional().nullable(),
  type: z.literal("monitor_alert"),
  alert_id: z.string(),
  condition_id: z.string(),
  condition_name: z.string(),
  condition_type: z.string(),
  scope: z.string(),
  detail: z.string(),
}).strict();

export const ServerMessageSchema = z.discriminatedUnion("type", [HelloMsgSchema, EventMsgSchema, StateMsgSchema, ChatResponseMsgSchema, EnvironmentsMsgSchema, RunAcceptedMsgSchema, AckMsgSchema, ErrorMsgSchema, ScenarioGeneratingMsgSchema, ScenarioPreviewMsgSchema, ScenarioReadyMsgSchema, ScenarioErrorMsgSchema, MonitorAlertMsgSchema]);

// --- Client -> Server messages ---

export const PauseCmdSchema = z.object({
  client_run_id: z.string().optional().nullable(),
  command_id: z.string().optional().nullable(),
  type: z.literal("pause"),
}).strict();

export const ResumeCmdSchema = z.object({
  client_run_id: z.string().optional().nullable(),
  command_id: z.string().optional().nullable(),
  type: z.literal("resume"),
}).strict();

export const InjectHintCmdSchema = z.object({
  client_run_id: z.string().optional().nullable(),
  command_id: z.string().optional().nullable(),
  type: z.literal("inject_hint"),
  text: z.string().min(1),
}).strict();

export const OverrideGateCmdSchema = z.object({
  client_run_id: z.string().optional().nullable(),
  command_id: z.string().optional().nullable(),
  type: z.literal("override_gate"),
  decision: z.enum(["advance", "retry", "rollback"]),
}).strict();

export const ChatAgentCmdSchema = z.object({
  client_run_id: z.string().optional().nullable(),
  command_id: z.string().optional().nullable(),
  type: z.literal("chat_agent"),
  role: z.string(),
  message: z.string().min(1),
}).strict();

export const StartRunCmdSchema = z.object({
  client_run_id: z.string().optional().nullable(),
  command_id: z.string().optional().nullable(),
  type: z.literal("start_run"),
  scenario: z.string(),
  generations: z.number().int().gt(0),
  require_playbook_approval: z.boolean().optional(),
}).strict();

export const ListScenariosCmdSchema = z.object({
  type: z.literal("list_scenarios"),
}).strict();

export const CreateScenarioCmdSchema = z.object({
  type: z.literal("create_scenario"),
  description: z.string().min(1),
}).strict();

export const ConfirmScenarioCmdSchema = z.object({
  type: z.literal("confirm_scenario"),
}).strict();

export const ReviseScenarioCmdSchema = z.object({
  type: z.literal("revise_scenario"),
  feedback: z.string().min(1),
}).strict();

export const CancelScenarioCmdSchema = z.object({
  type: z.literal("cancel_scenario"),
}).strict();

export const ClientMessageSchema = z.discriminatedUnion("type", [PauseCmdSchema, ResumeCmdSchema, InjectHintCmdSchema, OverrideGateCmdSchema, ChatAgentCmdSchema, StartRunCmdSchema, ListScenariosCmdSchema, CreateScenarioCmdSchema, ConfirmScenarioCmdSchema, ReviseScenarioCmdSchema, CancelScenarioCmdSchema]);

/** Parse a raw JSON string from the server into a typed message. Returns null on failure. */
export function parseServerMessage(raw: string) {
  try {
    const json = JSON.parse(raw);
    const result = ServerMessageSchema.safeParse(json);
    return result.success ? result.data : null;
  } catch {
    return null;
  }
}
