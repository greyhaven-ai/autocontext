import { describe, expect, it, vi } from "vitest";
import { z } from "zod";

import { AgentProgressNotePayloadSchema } from "../src/loop/agent-progress-note.js";
import { AgentTaskPlanPayloadSchema } from "../src/loop/agent-task-plan.js";
import { AgentTaskSpecSchema } from "../src/scenarios/agent-task-spec.js";
import { ImprovementTaskContractSchema } from "../src/scenarios/improvement-task-contract.js";
import { TaskDataSourceContentListSchema } from "../src/scenarios/task-data-source.js";
import { ClientMessageSchema } from "../src/server/protocol.js";
import { ImageAttachmentListSchema } from "../src/types/image-attachments.js";

const task = { taskPrompt: "Summarize the input", judgeRubric: "Accurate and clear" };
const contract = {
  objective: "Summarize the input",
  target: "The supplied document",
  deliverable: { description: "A concise summary" },
  criteria: "Accurate and clear",
};

const boundaries: {
  name: string;
  schema: z.ZodType;
  path: string[];
  input: (items: unknown[]) => unknown;
}[] = [
  { name: "image attachments", schema: ImageAttachmentListSchema, path: [], input: (items) => items },
  { name: "resolved task contents", schema: TaskDataSourceContentListSchema, path: [], input: (items) => items },
  { name: "agent task sources", schema: AgentTaskSpecSchema, path: ["taskDataSources"], input: (items) => ({ ...task, taskDataSources: items }) },
  { name: "improvement contract sources", schema: ImprovementTaskContractSchema, path: ["dataSources"], input: (items) => ({ ...contract, dataSources: items }) },
  {
    name: "task plan steps", schema: AgentTaskPlanPayloadSchema, path: ["steps"],
    input: (items) => ({ run_id: "run", plan_id: "plan", version: 1, plan_revision: 1, update_kind: "initial", active_step_id: null, steps: items }),
  },
  {
    name: "progress note evidence", schema: AgentProgressNotePayloadSchema, path: ["evidence_targets"],
    input: (items) => ({ run_id: "run", generation: 0, kind: "discovery", text: "Found relevant evidence", evidence_targets: items }),
  },
  {
    name: "chat_agent attachments", schema: ClientMessageSchema, path: ["image_attachments"],
    input: (items) => ({ type: "chat_agent", role: "coach", message: "Inspect the image", image_attachments: items }),
  },
  {
    name: "inject_hint attachments", schema: ClientMessageSchema, path: ["image_attachments"],
    input: (items) => ({ type: "inject_hint", text: "Inspect the image", image_attachments: items }),
  },
  {
    name: "create_task contents", schema: ClientMessageSchema, path: ["source_contents"],
    input: (items) => ({ type: "create_task", contract, source_contents: items }),
  },
];

describe("Zod collection resource bounds", () => {
  it.each(boundaries)("rejects oversized $name before reading any element", ({ schema, path, input }) => {
    const readElement = vi.fn();
    const items = new Proxy<unknown[]>(Array(2_000).fill(null), {
      get(target, key, receiver) {
        if (/^\d+$/.test(String(key))) readElement();
        return Reflect.get(target, key, receiver);
      },
    });

    const result = schema.safeParse(input(items));

    expect(result.success).toBe(false);
    if (result.success) throw new Error("oversized input was accepted");
    expect(result.error.issues).toEqual([
      expect.objectContaining({ code: "too_big", path }),
    ]);
    expect(readElement).not.toHaveBeenCalled();
  });

  it.each(boundaries)("still validates malformed in-bounds $name", ({ schema, path, input }) => {
    const result = schema.safeParse(input([null]));

    expect(result.success).toBe(false);
    if (result.success) throw new Error("malformed element was accepted");
    expect(result.error.issues.some((issue) => issue.path.slice(0, path.length + 1).join(".") === [...path, 0].join("."))).toBe(true);
  });

  it("also rejects sparse oversized arrays without invoking an element getter", () => {
    const items: unknown[] = new Array(2_000);
    Object.defineProperty(items, 0, { get: () => { throw new Error("element was read"); } });

    expect(ImageAttachmentListSchema.safeParse(items).success).toBe(false);
    expect(TaskDataSourceContentListSchema.safeParse(items).success).toBe(false);
  });

  it("preserves valid task records and resolved content", () => {
    const input = { ...task, difficultyTiers: [{ name: "hard" }], calibrationExamples: [{ score: 0.8 }] };
    const parsed = AgentTaskSpecSchema.parse(input);

    expect(parsed.difficultyTiers).toEqual(input.difficultyTiers);
    expect(parsed.calibrationExamples).toEqual(input.calibrationExamples);
    expect(TaskDataSourceContentListSchema.parse([{ sourceId: "input", content: "valid" }])).toEqual([{ sourceId: "input", content: "valid" }]);
  });
});
