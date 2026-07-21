import { describe, expect, it } from "vitest";

import {
  AGENT_TASK_PLAN_EVENT_NAME,
  AgentTaskPlanPayloadSchema,
  createAgentTaskPlanPublisher,
  type AgentTaskPlanPayload,
} from "../src/loop/agent-task-plan.js";
import {
  sanitizeRunTranscriptMessage,
  sanitizeRunTranscriptText,
} from "../src/server/run-transcript-frame.js";

const CREDENTIAL_SHAPED_IDS = [
  "AKIAABCDEFGHIJKLMNOP",
  "AIzaabcdefghijklmnopqrstuvwxyz123456",
  "gho_abcdefghijklmnopqrstuvwxyz123456",
  "glpat-abcdefghijklmnopqrstuvwxyz",
  "lin_api_abcdefghijklmnopqrstuvwxyz123456",
  "npm_abcdefghijklmnopqrstuvwxyz123456",
  "pk-abcdefghijklmnopqrstuvwxyz",
  "pypi-AgEIabcdefghijklmnopqrstuvwxyz123456",
  "SG.abcdefghijklmnopqrstuvwxyz123456",
] as const;

function validPayload(): AgentTaskPlanPayload {
  return {
    run_id: "run-1",
    plan_id: "plan-1",
    version: 1,
    plan_revision: 1,
    update_kind: "initial",
    active_step_id: "inspect",
    summary: "Starting",
    steps: [
      { id: "inspect", label: "Inspect", status: "in_progress" },
      { id: "change", label: "Change", status: "pending" },
    ],
  };
}

describe("agent task plan protocol", () => {
  it("enforces strict identity, step, active-step, and aggregate invariants", () => {
    expect(AgentTaskPlanPayloadSchema.safeParse(validPayload()).success).toBe(true);
    expect(
      AgentTaskPlanPayloadSchema.safeParse({
        ...validPayload(),
        unexpected: true,
      }).success,
    ).toBe(false);
    expect(
      AgentTaskPlanPayloadSchema.safeParse({
        ...validPayload(),
        plan_id: "ghp_abcdefghijklmnopqrstuvwxyz123456",
      }).success,
    ).toBe(false);
    expect(
      AgentTaskPlanPayloadSchema.safeParse({
        ...validPayload(),
        plan_id: "plan-dp_abcdefghijklmnopqrstuvwxyz",
      }).success,
    ).toBe(false);
    expect(
      AgentTaskPlanPayloadSchema.safeParse({
        ...validPayload(),
        steps: [
          ...validPayload().steps,
          { id: "inspect", label: "Duplicate", status: "pending" },
        ],
      }).success,
    ).toBe(false);
    expect(
      AgentTaskPlanPayloadSchema.safeParse({
        ...validPayload(),
        active_step_id: "change",
      }).success,
    ).toBe(false);
    expect(
      AgentTaskPlanPayloadSchema.safeParse({
        ...validPayload(),
        summary: "x".repeat(241),
      }).success,
    ).toBe(false);
    expect(
      AgentTaskPlanPayloadSchema.safeParse({
        ...validPayload(),
        summary: "   ",
      }).success,
    ).toBe(false);
    expect(
      AgentTaskPlanPayloadSchema.safeParse({
        ...validPayload(),
        steps: [{ id: "inspect", label: "Inspect", detail: " ", status: "in_progress" }],
      }).success,
    ).toBe(false);
    expect(
      AgentTaskPlanPayloadSchema.safeParse({
        ...validPayload(),
        steps: Array.from({ length: 50 }, (_, index) => ({
          id: `step-${index}`,
          label: "x".repeat(160),
          detail: "x".repeat(400),
          status: "pending" as const,
        })),
        active_step_id: null,
      }).success,
    ).toBe(false);
  });

  it.each(CREDENTIAL_SHAPED_IDS)("rejects and redacts credential-shaped ID %s", (id) => {
    expect(
      AgentTaskPlanPayloadSchema.safeParse({
        ...validPayload(),
        plan_id: id,
      }).success,
    ).toBe(false);
    expect(sanitizeRunTranscriptText(`credential ${id}`)).not.toContain(id);
  });

  it("publishes monotonic full snapshots, increments replan revision, and keeps completion sticky", () => {
    const emissions: Array<{ event: string; payload: AgentTaskPlanPayload }> = [];
    const publisher = createAgentTaskPlanPublisher({
      runId: "run-1",
      planId: "plan-1",
      steps: [
        { id: "inspect", label: "Inspect" },
        { id: "change", label: "Change" },
        { id: "verify", label: "Verify" },
      ],
      events: {
        emit(event, payload) {
          emissions.push({ event, payload: AgentTaskPlanPayloadSchema.parse(payload) });
        },
      },
    });

    expect(publisher).not.toBeNull();
    expect(publisher?.initial({ activeStepId: "inspect", summary: "Starting" })).toBe(true);
    expect(
      publisher?.progress({
        activeStepId: "change",
        completedStepIds: ["inspect"],
        summary: "Implementing",
      }),
    ).toBe(true);
    expect(
      publisher?.replan({
        activeStepId: "verify",
        completedStepIds: ["inspect"],
        skippedStepIds: ["change"],
        summary: "Verification is now the best next step",
        stepDetails: { verify: { detail: "Run focused tests" } },
      }),
    ).toBe(true);
    expect(
      publisher?.progress({
        activeStepId: "verify",
        summary: "Still verifying",
      }),
    ).toBe(true);

    expect(emissions.map(({ event }) => event)).toEqual([
      AGENT_TASK_PLAN_EVENT_NAME,
      AGENT_TASK_PLAN_EVENT_NAME,
      AGENT_TASK_PLAN_EVENT_NAME,
      AGENT_TASK_PLAN_EVENT_NAME,
    ]);
    expect(emissions.map(({ payload }) => payload.version)).toEqual([1, 2, 3, 4]);
    expect(emissions.map(({ payload }) => payload.plan_revision)).toEqual([1, 1, 2, 2]);
    expect(emissions.map(({ payload }) => payload.update_kind)).toEqual([
      "initial",
      "progress",
      "replan",
      "progress",
    ]);
    expect(emissions.at(-1)?.payload.steps).toEqual([
      { id: "inspect", label: "Inspect", status: "completed" },
      { id: "change", label: "Change", status: "skipped" },
      {
        id: "verify",
        label: "Verify",
        detail: "Run focused tests",
        status: "in_progress",
      },
    ]);
    for (const emission of emissions) {
      expect(
        sanitizeRunTranscriptMessage({
          type: "event",
          event: emission.event,
          payload: emission.payload,
        }),
      ).not.toBeNull();
    }
  });

  it.each(["completed", "failed", "interrupted"] as const)(
    "emits a terminal %s snapshot with no active step",
    (status) => {
      const emissions: AgentTaskPlanPayload[] = [];
      const publisher = createAgentTaskPlanPublisher({
        runId: "run-terminal",
        steps: [
          { id: "first", label: "First" },
          { id: "second", label: "Second" },
        ],
        events: {
          emit(_event, payload) {
            emissions.push(AgentTaskPlanPayloadSchema.parse(payload));
          },
        },
      });
      publisher?.initial({ activeStepId: "first" });
      expect(publisher?.terminal(status)).toBe(true);
      const terminal = emissions.at(-1);
      expect(terminal?.active_step_id).toBeNull();
      expect(terminal?.steps.map((step) => step.status)).toEqual(
        status === "completed" ? ["completed", "completed"] : [status, "skipped"],
      );
      expect(publisher?.terminal(status)).toBe(false);
    },
  );

  it("redacts plan copy before the raw event emitter sees it", () => {
    const rawPayloads: Record<string, unknown>[] = [];
    const publisher = createAgentTaskPlanPublisher({
      runId: "run-redaction",
      steps: [
        {
          id: "inspect",
          label: "  Inspect token=super-secret-value  ",
          detail: "Authorization: Bearer another-secret-value",
        },
      ],
      events: {
        emit(_event, payload) {
          rawPayloads.push(payload);
        },
      },
    });
    expect(
      publisher?.initial({
        activeStepId: "inspect",
        summary: "Using ghp_abcdefghijklmnopqrstuvwxyz123456",
      }),
    ).toBe(true);
    const wire = JSON.stringify(rawPayloads);
    expect(wire).toContain("[Redacted]");
    expect(wire).not.toContain("super-secret-value");
    expect(wire).not.toContain("another-secret-value");
    expect(wire).not.toContain("ghp_abcdefghijklmnopqrstuvwxyz123456");
    expect((rawPayloads.at(0)?.steps as Array<{ label: string }>).at(0)?.label).toBe(
      "Inspect [Redacted]",
    );
  });

  it("rejects empty copy and credential-shaped IDs before publishing", () => {
    expect(
      createAgentTaskPlanPublisher({
        runId: "run-dp_abcdefghijklmnopqrstuvwxyz",
        steps: [{ id: "inspect", label: "Inspect" }],
        events: { emit() {} },
      }),
    ).toBeNull();
    expect(
      createAgentTaskPlanPublisher({
        runId: "run-valid",
        steps: [{ id: "inspect", label: "   " }],
        events: { emit() {} },
      }),
    ).toBeNull();
  });

  it("rejects a complete oversized snapshot atomically", () => {
    const rawPayloads: Record<string, unknown>[] = [];
    const publisher = createAgentTaskPlanPublisher({
      runId: "run-oversized",
      steps: Array.from({ length: 20 }, (_, index) => ({
        id: `step-${index}`,
        label: `Step ${index}`,
        detail: "x".repeat(600),
      })),
      events: { emit: (_event, payload) => rawPayloads.push(payload) },
    });
    expect(publisher).not.toBeNull();
    expect(
      AgentTaskPlanPayloadSchema.safeParse({
        ...validPayload(),
        active_step_id: "step-0",
        steps: Array.from({ length: 20 }, (_, index) => ({
          id: `step-${index}`,
          label: `Step ${index}`,
          detail: "x".repeat(600),
          status: index === 0 ? "in_progress" : "pending",
        })),
      }).success,
    ).toBe(true);
    expect(publisher?.initial({ activeStepId: "step-0" })).toBe(false);
    expect(rawPayloads).toEqual([]);
  });
});
