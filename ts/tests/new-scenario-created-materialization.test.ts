import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import { executeCreatedScenarioMaterialization } from "../src/cli/new-scenario-created-materialization.js";
import { materializeScenario } from "../src/scenarios/materialize.js";
import { createScenarioFromDescription } from "../src/scenarios/scenario-creator.js";

describe("new-scenario created materialization", () => {
  const tempDirs: string[] = [];

  afterEach(() => {
    for (const dir of tempDirs.splice(0)) {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("routes prepared materialization directly instead of through an extra execution wrapper", () => {
    const cliDir = join(import.meta.dirname, "..", "src", "cli");
    const source = readFileSync(join(cliDir, "new-scenario-created-materialization.ts"), "utf-8");

    expect(source).not.toContain("new-scenario-created-materialization-execution");
    expect(existsSync(join(cliDir, "new-scenario-created-materialization-execution.ts"))).toBe(
      false,
    );
  });

  it("materializes a created scenario and renders the created result", async () => {
    const materializeScenario = vi.fn(async () => ({
      scenarioDir: "/tmp/fresh_task",
      generatedSource: true,
      persisted: true,
      errors: [],
    }));

    await expect(
      executeCreatedScenarioMaterialization({
        created: {
          name: "fresh_task",
          family: "agent_task",
          spec: {
            taskPrompt: "Summarize the incident report.",
            rubric: "Clarity and factual accuracy",
            description: "Evaluate incident summaries",
          },
        },
        materializeScenario,
        knowledgeRoot: "/tmp/knowledge",
        json: false,
      }),
    ).resolves.toBe(
      [
        "Materialized scenario: fresh_task (family: agent_task)",
        "  Directory: /tmp/fresh_task",
        "  Task prompt: Summarize the incident report.",
        "  Rubric: Clarity and factual accuracy",
        "  Generated: scenario.js",
      ].join("\n"),
    );

    expect(materializeScenario).toHaveBeenCalledWith({
      name: "fresh_task",
      family: "agent_task",
      spec: {
        taskPrompt: "Summarize the incident report.",
        rubric: "Clarity and factual accuracy",
        description: "Evaluate incident summaries",
      },
      knowledgeRoot: "/tmp/knowledge",
    });
  });

  it("materializes a core-only simulation fallback as an agent_task instead of failing codegen", async () => {
    const knowledgeRoot = mkdtempSync(join(tmpdir(), "ac559-new-scenario-"));
    tempDirs.push(knowledgeRoot);

    const provider = {
      defaultModel: () => "mock-model",
      complete: vi.fn(async ({ systemPrompt }: { systemPrompt?: string }) => {
        const bareFallback = {
          family: "simulation",
          name: "paperclip_test",
          taskPrompt: "Write a memo that resists optimizing for the visible metric.",
          rubric: "Reward usefulness to the real audience over metric gaming.",
          description: "A core-only fallback payload without simulation actions.",
        };

        return {
          text: JSON.stringify(bareFallback),
          model: "mock-model",
          usage: { inputTokens: 0, outputTokens: 0 },
        };
      }),
    };

    const created = await createScenarioFromDescription(
      "Create a paperclip-test simulation where the model can exploit a visible metric instead of serving the real task objective.",
      provider as never,
    );

    const materialized = await materializeScenario({
      name: created.name,
      family: created.family as never,
      spec: created.spec,
      knowledgeRoot,
    });

    expect(created.family).toBe("agent_task");
    expect(materialized.persisted).toBe(true);
    expect(materialized.generatedSource).toBe(false);
    expect(materialized.errors).toEqual([]);
  });
});
