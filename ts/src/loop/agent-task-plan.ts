import { z } from "zod";

import {
  isCredentialShapedPresentationId,
  redactPresentationText,
} from "../security/presentation-redaction.js";

export const AGENT_TASK_PLAN_CAPABILITY = "agent_task_plan_v1";
export const AGENT_TASK_PLAN_EVENT_NAME = "task_plan_updated";

export const MAX_RETAINED_AGENT_TASK_PLAN_BYTES = 12 * 1_024;
export const MAX_AGENT_TASK_PLAN_STEPS = 50;
export const MAX_AGENT_TASK_PLAN_STRING_CHARACTERS = 20_000;

const MAX_ID_LENGTH = 200;
const MAX_LABEL_LENGTH = 160;
const MAX_DETAIL_LENGTH = 2_000;
const MAX_SUMMARY_LENGTH = 240;
const SAFE_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]*$/;

export const AgentTaskPlanIdSchema = z
  .string()
  .min(1)
  .max(MAX_ID_LENGTH)
  .regex(SAFE_ID_PATTERN)
  .refine(
    (value) => !isCredentialShapedPresentationId(value),
    "credential-shaped IDs are not allowed",
  );

export const AgentTaskPlanStepStatusSchema = z.enum([
  "pending",
  "in_progress",
  "completed",
  "blocked",
  "failed",
  "skipped",
  "interrupted",
]);

export const AgentTaskPlanStepSchema = z
  .object({
    id: AgentTaskPlanIdSchema,
    label: z.string().trim().min(1).max(MAX_LABEL_LENGTH),
    detail: z.string().trim().min(1).max(MAX_DETAIL_LENGTH).optional(),
    status: AgentTaskPlanStepStatusSchema,
  })
  .strict();

export const AgentTaskPlanPayloadSchema = z
  .object({
    run_id: AgentTaskPlanIdSchema,
    plan_id: AgentTaskPlanIdSchema,
    version: z.number().int().positive(),
    plan_revision: z.number().int().positive(),
    update_kind: z.enum(["initial", "progress", "replan"]),
    active_step_id: AgentTaskPlanIdSchema.nullable(),
    summary: z.string().trim().min(1).max(MAX_SUMMARY_LENGTH).optional(),
    steps: z.array(AgentTaskPlanStepSchema).min(1).max(MAX_AGENT_TASK_PLAN_STEPS),
  })
  .strict()
  .superRefine((value, context) => {
    const ids = new Set<string>();
    let activeCount = 0;
    let aggregateCharacters =
      value.run_id.length +
      value.plan_id.length +
      (value.active_step_id?.length ?? 0) +
      (value.summary?.length ?? 0);

    for (const [index, step] of value.steps.entries()) {
      if (ids.has(step.id)) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          message: "step IDs must be unique",
          path: ["steps", index, "id"],
        });
      }
      ids.add(step.id);
      aggregateCharacters += step.id.length + step.label.length + (step.detail?.length ?? 0);
      if (step.status === "in_progress") activeCount += 1;
    }

    if (activeCount > 1) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "at most one step may be in progress",
        path: ["steps"],
      });
    }

    const activeStep = value.steps.find((step) => step.id === value.active_step_id);
    if (
      (value.active_step_id === null && activeCount !== 0) ||
      (value.active_step_id !== null &&
        (activeCount !== 1 || activeStep?.status !== "in_progress"))
    ) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "active_step_id must identify the only in-progress step",
        path: ["active_step_id"],
      });
    }

    if (aggregateCharacters > MAX_AGENT_TASK_PLAN_STRING_CHARACTERS) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "task plan exceeds its aggregate character budget",
        path: [],
      });
    }
  });

export type AgentTaskPlanStepStatus = z.infer<typeof AgentTaskPlanStepStatusSchema>;
export type AgentTaskPlanStep = z.infer<typeof AgentTaskPlanStepSchema>;
export type AgentTaskPlanPayload = z.infer<typeof AgentTaskPlanPayloadSchema>;

export interface AgentTaskPlanStepInput {
  id: string;
  label: string;
  detail?: string;
}

export interface AgentTaskPlanStepDetailUpdate {
  detail?: string;
  label?: string;
}

export type AgentTaskPlanStepDetails = Readonly<
  Record<string, AgentTaskPlanStepDetailUpdate>
>;

interface AgentTaskPlanUpdateInput {
  activeStepId: string | null;
  completedStepIds?: readonly string[];
  skippedStepIds?: readonly string[];
  stepDetails?: AgentTaskPlanStepDetails;
  summary?: string;
}

export interface AgentTaskPlanPublisher {
  initial(input: Pick<AgentTaskPlanUpdateInput, "activeStepId" | "stepDetails" | "summary">): boolean;
  progress(input: AgentTaskPlanUpdateInput): boolean;
  replan(input: AgentTaskPlanUpdateInput & { summary: string }): boolean;
  terminal(
    status: "completed" | "failed" | "interrupted",
    input?: Pick<AgentTaskPlanUpdateInput, "stepDetails" | "summary">,
  ): boolean;
}

interface AgentTaskPlanEventSink {
  emit(event: string, payload: Record<string, unknown>): void;
}

export interface CreateAgentTaskPlanPublisherOptions {
  events: AgentTaskPlanEventSink;
  planId?: string;
  runId: string;
  steps: readonly AgentTaskPlanStepInput[];
}

function sanitizeCopy(value: string, maxLength: number): string {
  const redacted = redactPresentationText(value.trim());
  if (redacted.length <= maxLength) return redacted;
  return `${redacted.slice(0, maxLength - 1)}…`;
}

function sanitizeStep(step: AgentTaskPlanStep): AgentTaskPlanStep {
  return {
    id: step.id,
    label: sanitizeCopy(step.label, MAX_LABEL_LENGTH),
    ...(step.detail === undefined
      ? {}
      : { detail: sanitizeCopy(step.detail, MAX_DETAIL_LENGTH) }),
    status: step.status,
  };
}

export function sanitizeAgentTaskPlanPayload(value: unknown): AgentTaskPlanPayload | null {
  const parsed = AgentTaskPlanPayloadSchema.safeParse(value);
  if (!parsed.success) return null;
  const sanitized = {
    ...parsed.data,
    ...(parsed.data.summary === undefined
      ? {}
      : { summary: sanitizeCopy(parsed.data.summary, MAX_SUMMARY_LENGTH) }),
    steps: parsed.data.steps.map(sanitizeStep),
  };
  const safe = AgentTaskPlanPayloadSchema.safeParse(sanitized);
  if (!safe.success) return null;
  if (!isAgentTaskPlanPayloadRetainable(safe.data)) {
    return null;
  }
  return safe.data;
}

export function isAgentTaskPlanPayloadRetainable(payload: AgentTaskPlanPayload): boolean {
  const message = {
    type: "event",
    event: AGENT_TASK_PLAN_EVENT_NAME,
    payload,
  };
  return (
    Buffer.byteLength(JSON.stringify(message), "utf-8") <=
    MAX_RETAINED_AGENT_TASK_PLAN_BYTES
  );
}

function applyStepDetails(
  steps: readonly AgentTaskPlanStep[],
  stepDetails: AgentTaskPlanStepDetails | undefined,
): AgentTaskPlanStep[] {
  if (!stepDetails) return steps.map((step) => ({ ...step }));
  return steps.map((step) => {
    const update = stepDetails[step.id];
    if (!update) return { ...step };
    return {
      ...step,
      ...(update.label === undefined
        ? {}
        : { label: sanitizeCopy(update.label, MAX_LABEL_LENGTH) }),
      ...(update.detail === undefined
        ? {}
        : { detail: sanitizeCopy(update.detail, MAX_DETAIL_LENGTH) }),
    };
  });
}

function hasOnlyKnownStepIds(ids: readonly string[] | undefined, knownIds: Set<string>): boolean {
  return ids === undefined || ids.every((id) => knownIds.has(id));
}

export function createAgentTaskPlanPublisher(
  options: CreateAgentTaskPlanPublisherOptions,
): AgentTaskPlanPublisher | null {
  const runIdResult = AgentTaskPlanIdSchema.safeParse(options.runId);
  const planIdResult = AgentTaskPlanIdSchema.safeParse(options.planId ?? "task-plan");
  if (!runIdResult.success || !planIdResult.success) return null;
  if (options.steps.length < 1 || options.steps.length > MAX_AGENT_TASK_PLAN_STEPS) return null;

  const stepIds = new Set<string>();
  const initialSteps: AgentTaskPlanStep[] = [];
  for (const step of options.steps) {
    const id = AgentTaskPlanIdSchema.safeParse(step.id);
    if (!id.success || stepIds.has(id.data)) return null;
    stepIds.add(id.data);
    const initialStep = AgentTaskPlanStepSchema.safeParse({
      id: id.data,
      label: sanitizeCopy(step.label, MAX_LABEL_LENGTH),
      ...(step.detail === undefined
        ? {}
        : { detail: sanitizeCopy(step.detail, MAX_DETAIL_LENGTH) }),
      status: "pending",
    });
    if (!initialStep.success) return null;
    initialSteps.push(initialStep.data);
  }

  let steps = initialSteps;
  let version = 0;
  let planRevision = 1;
  let terminal = false;

  const publish = (
    updateKind: AgentTaskPlanPayload["update_kind"],
    activeStepId: string | null,
    candidateSteps: readonly AgentTaskPlanStep[],
    summary?: string,
    nextPlanRevision = planRevision,
  ): boolean => {
    if (terminal || (activeStepId !== null && !stepIds.has(activeStepId))) return false;
    const payload = sanitizeAgentTaskPlanPayload({
      run_id: runIdResult.data,
      plan_id: planIdResult.data,
      version: version + 1,
      plan_revision: nextPlanRevision,
      update_kind: updateKind,
      active_step_id: activeStepId,
      ...(summary === undefined ? {} : { summary }),
      steps: candidateSteps,
    });
    if (!payload) return false;
    try {
      options.events.emit(AGENT_TASK_PLAN_EVENT_NAME, payload);
    } catch {
      return false;
    }
    steps = payload.steps.map((step) => ({ ...step }));
    version = payload.version;
    planRevision = payload.plan_revision;
    return true;
  };

  const update = (
    input: AgentTaskPlanUpdateInput,
    updateKind: "progress" | "replan",
  ): boolean => {
    if (
      !hasOnlyKnownStepIds(input.completedStepIds, stepIds) ||
      !hasOnlyKnownStepIds(input.skippedStepIds, stepIds)
    ) {
      return false;
    }
    const completed = new Set(input.completedStepIds ?? []);
    const skipped = new Set(input.skippedStepIds ?? []);
    let candidate = applyStepDetails(steps, input.stepDetails).map((step) => {
      if (step.status === "completed" || completed.has(step.id)) {
        return { ...step, status: "completed" as const };
      }
      if (skipped.has(step.id)) return { ...step, status: "skipped" as const };
      if (step.id === input.activeStepId) return { ...step, status: "in_progress" as const };
      if (step.status === "in_progress") return { ...step, status: "pending" as const };
      return step;
    });
    if (input.activeStepId !== null) {
      candidate = candidate.map((step) =>
        step.id === input.activeStepId && step.status !== "completed"
          ? { ...step, status: "in_progress" as const }
          : step,
      );
    }
    const nextRevision = updateKind === "replan" ? planRevision + 1 : planRevision;
    return publish(updateKind, input.activeStepId, candidate, input.summary, nextRevision);
  };

  return {
    initial(input) {
      if (version !== 0) return false;
      const candidate = applyStepDetails(steps, input.stepDetails).map((step) =>
        step.id === input.activeStepId
          ? { ...step, status: "in_progress" as const }
          : step,
      );
      return publish("initial", input.activeStepId, candidate, input.summary);
    },
    progress(input) {
      if (version === 0) return false;
      return update(input, "progress");
    },
    replan(input) {
      if (version === 0) return false;
      return update(input, "replan");
    },
    terminal(status, input = {}) {
      if (version === 0 || terminal) return false;
      const activeStep = steps.find((step) => step.status === "in_progress");
      const candidate = applyStepDetails(steps, input.stepDetails).map((step) => {
        if (step.status === "completed" || step.status === "skipped") return step;
        if (status === "completed") return { ...step, status: "completed" as const };
        if (activeStep?.id === step.id) return { ...step, status };
        return { ...step, status: "skipped" as const };
      });
      const emitted = publish("progress", null, candidate, input.summary);
      if (emitted) terminal = true;
      return emitted;
    },
  };
}
