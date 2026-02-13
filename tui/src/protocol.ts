import { z } from "zod";

// --- Server -> Client schemas ---

export const ServerEventSchema = z.object({
  type: z.literal("event"),
  event: z.string(),
  payload: z.record(z.unknown()),
});

export const ServerStateSchema = z.object({
  type: z.literal("state"),
  paused: z.boolean(),
  generation: z.number(),
  phase: z.string(),
});

export const ServerChatResponseSchema = z.object({
  type: z.literal("chat_response"),
  role: z.string(),
  text: z.string(),
});

export const ExecutorResourcesSchema = z.object({
  docker_image: z.string(),
  cpu_cores: z.number(),
  memory_gb: z.number(),
  disk_gb: z.number(),
  timeout_minutes: z.number(),
});

export const ScenarioInfoSchema = z.object({
  name: z.string(),
  description: z.string(),
});

export const ExecutorInfoSchema = z.object({
  mode: z.string(),
  available: z.boolean(),
  description: z.string(),
  resources: ExecutorResourcesSchema.optional(),
});

export const ServerEnvironmentsSchema = z.object({
  type: z.literal("environments"),
  scenarios: z.array(ScenarioInfoSchema),
  executors: z.array(ExecutorInfoSchema),
  current_executor: z.string(),
  agent_provider: z.string(),
});

export const ServerRunAcceptedSchema = z.object({
  type: z.literal("run_accepted"),
  run_id: z.string(),
  scenario: z.string(),
  generations: z.number(),
});

export const ServerAckSchema = z.object({
  type: z.literal("ack"),
  action: z.string(),
  decision: z.string().optional(),
});

export const ServerErrorSchema = z.object({
  type: z.literal("error"),
  message: z.string(),
});

export const ServerScenarioGeneratingSchema = z.object({
  type: z.literal("scenario_generating"),
  name: z.string(),
});

export const ServerScenarioPreviewSchema = z.object({
  type: z.literal("scenario_preview"),
  name: z.string(),
  display_name: z.string(),
  description: z.string(),
  strategy_params: z.array(z.object({ name: z.string(), description: z.string() })),
  scoring_components: z.array(z.object({ name: z.string(), description: z.string(), weight: z.number() })),
  constraints: z.array(z.string()),
  win_threshold: z.number(),
});

export const ServerScenarioReadySchema = z.object({
  type: z.literal("scenario_ready"),
  name: z.string(),
  test_scores: z.array(z.number()),
});

export const ServerScenarioErrorSchema = z.object({
  type: z.literal("scenario_error"),
  message: z.string(),
});

export const ServerMessageSchema = z.discriminatedUnion("type", [
  ServerEventSchema,
  ServerStateSchema,
  ServerChatResponseSchema,
  ServerEnvironmentsSchema,
  ServerRunAcceptedSchema,
  ServerAckSchema,
  ServerErrorSchema,
  ServerScenarioGeneratingSchema,
  ServerScenarioPreviewSchema,
  ServerScenarioReadySchema,
  ServerScenarioErrorSchema,
]);

// --- Client -> Server schemas ---

export const ClientPauseSchema = z.object({
  type: z.literal("pause"),
});

export const ClientResumeSchema = z.object({
  type: z.literal("resume"),
});

export const ClientInjectHintSchema = z.object({
  type: z.literal("inject_hint"),
  text: z.string().min(1),
});

export const ClientOverrideGateSchema = z.object({
  type: z.literal("override_gate"),
  decision: z.enum(["advance", "retry", "rollback"]),
});

export const ClientChatAgentSchema = z.object({
  type: z.literal("chat_agent"),
  role: z.string(),
  message: z.string().min(1),
});

export const ClientStartRunSchema = z.object({
  type: z.literal("start_run"),
  scenario: z.string(),
  generations: z.number().int().positive(),
});

export const ClientListScenariosSchema = z.object({
  type: z.literal("list_scenarios"),
});

export const ClientCreateScenarioSchema = z.object({
  type: z.literal("create_scenario"),
  description: z.string().min(1),
});

export const ClientConfirmScenarioSchema = z.object({
  type: z.literal("confirm_scenario"),
});

export const ClientReviseScenarioSchema = z.object({
  type: z.literal("revise_scenario"),
  feedback: z.string().min(1),
});

export const ClientCancelScenarioSchema = z.object({
  type: z.literal("cancel_scenario"),
});

export const ClientMessageSchema = z.discriminatedUnion("type", [
  ClientPauseSchema,
  ClientResumeSchema,
  ClientInjectHintSchema,
  ClientOverrideGateSchema,
  ClientChatAgentSchema,
  ClientStartRunSchema,
  ClientListScenariosSchema,
  ClientCreateScenarioSchema,
  ClientConfirmScenarioSchema,
  ClientReviseScenarioSchema,
  ClientCancelScenarioSchema,
]);

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
