// AUTO-GENERATED from mts/src/mts/server/protocol.py
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
});

export const ExecutorInfoSchema = z.object({
  mode: z.string(),
  available: z.boolean(),
  description: z.string(),
  resources: ExecutorResourcesSchema.optional().nullable(),
});

export const ScenarioInfoSchema = z.object({
  name: z.string(),
  description: z.string(),
});

export const ScoringComponentSchema = z.object({
  name: z.string(),
  description: z.string(),
  weight: z.number(),
});

export const StrategyParamSchema = z.object({
  name: z.string(),
  description: z.string(),
});

// --- Server -> Client messages ---

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
  decision: z.string().optional().nullable(),
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
  stage: z.string(),
});

export const ServerMessageSchema = z.discriminatedUnion("type", [HelloMsgSchema, EventMsgSchema, StateMsgSchema, ChatResponseMsgSchema, EnvironmentsMsgSchema, RunAcceptedMsgSchema, AckMsgSchema, ErrorMsgSchema, ScenarioGeneratingMsgSchema, ScenarioPreviewMsgSchema, ScenarioReadyMsgSchema, ScenarioErrorMsgSchema]);

// --- Client -> Server messages ---

export const PauseCmdSchema = z.object({
  type: z.literal("pause"),
});

export const ResumeCmdSchema = z.object({
  type: z.literal("resume"),
});

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
  message: z.string().min(1),
});

export const StartRunCmdSchema = z.object({
  type: z.literal("start_run"),
  scenario: z.string(),
  generations: z.number().int().gt(0),
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
