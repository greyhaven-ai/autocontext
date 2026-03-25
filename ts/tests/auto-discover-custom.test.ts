/**
 * Tests for AC-379: Auto-discover custom scenarios from knowledge/ directory.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";
import type { LLMProvider } from "../src/types/index.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-discover-"));
}

function makeMockProvider(response = "mock draft"): LLMProvider {
  return {
    name: "mock",
    defaultModel: () => "mock-model",
    complete: async (opts) => {
      if (opts.systemPrompt.includes("expert judge")) {
        return {
          text:
            "<!-- JUDGE_RESULT_START -->\n" +
            JSON.stringify({
              score: 0.9,
              reasoning: "Loaded saved spec and completed successfully.",
              dimensions: { quality: 0.9 },
            }) +
            "\n<!-- JUDGE_RESULT_END -->",
          usage: {},
        };
      }
      return { text: response, usage: {} };
    },
  };
}

// ---------------------------------------------------------------------------
// Auto-discovery at startup
// ---------------------------------------------------------------------------

describe("Custom scenario auto-discovery", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("discoverAndRegisterCustomScenarios loads from knowledge dir", async () => {
    const { discoverAndRegisterCustomScenarios } = await import("../src/scenarios/custom-loader.js");

    const customDir = join(dir, "_custom_scenarios");
    const scenarioDir = join(customDir, "test_summarization");
    mkdirSync(scenarioDir, { recursive: true });
    writeFileSync(join(scenarioDir, "scenario_type.txt"), "agent_task", "utf-8");
    writeFileSync(join(scenarioDir, "spec.json"), JSON.stringify({
      name: "test_summarization",
      taskPrompt: "Summarize this document.",
      rubric: "Evaluate completeness.",
      description: "Test summarization task.",
    }), "utf-8");

    const count = discoverAndRegisterCustomScenarios(dir);
    expect(count).toBe(1);
  });

  it("discovered scenarios appear in CUSTOM_SCENARIO_REGISTRY", async () => {
    const { discoverAndRegisterCustomScenarios, CUSTOM_SCENARIO_REGISTRY } = await import("../src/scenarios/custom-loader.js");

    const customDir = join(dir, "_custom_scenarios");
    const scenarioDir = join(customDir, "code_review");
    mkdirSync(scenarioDir, { recursive: true });
    writeFileSync(join(scenarioDir, "spec.json"), JSON.stringify({
      name: "code_review",
      taskPrompt: "Review this code.",
      rubric: "Thoroughness.",
      description: "Code review task.",
    }), "utf-8");

    discoverAndRegisterCustomScenarios(dir);
    expect(CUSTOM_SCENARIO_REGISTRY.has("code_review")).toBe(true);
  });

  it("discovered agent tasks have factories in CUSTOM_AGENT_TASK_REGISTRY", async () => {
    const { discoverAndRegisterCustomScenarios, CUSTOM_AGENT_TASK_REGISTRY } = await import("../src/scenarios/custom-loader.js");

    const customDir = join(dir, "_custom_scenarios");
    const scenarioDir = join(customDir, "incident_triage");
    mkdirSync(scenarioDir, { recursive: true });
    writeFileSync(join(scenarioDir, "scenario_type.txt"), "agent_task", "utf-8");
    writeFileSync(join(scenarioDir, "spec.json"), JSON.stringify({
      name: "incident_triage",
      taskPrompt: "Triage this incident.",
      rubric: "Speed and accuracy.",
      description: "Incident triage task.",
    }), "utf-8");

    discoverAndRegisterCustomScenarios(dir);
    expect(typeof CUSTOM_AGENT_TASK_REGISTRY.incident_triage).toBe("function");
  });

  it("returns 0 when knowledge dir has no _custom_scenarios", async () => {
    const { discoverAndRegisterCustomScenarios } = await import("../src/scenarios/custom-loader.js");
    const count = discoverAndRegisterCustomScenarios(dir);
    expect(count).toBe(0);
  });

  it("queued saved custom scenarios run end-to-end through TaskRunner", async () => {
    const { SQLiteStore } = await import("../src/storage/index.js");
    const { TaskRunner, enqueueTask } = await import("../src/execution/task-runner.js");

    // Create custom scenario in the knowledge dir
    const customDir = join(dir, "knowledge", "_custom_scenarios", "my_task");
    mkdirSync(customDir, { recursive: true });
    writeFileSync(join(customDir, "spec.json"), JSON.stringify({
      name: "my_task",
      taskPrompt: "Do something.",
      rubric: "Evaluate.",
      description: "Test task.",
    }), "utf-8");

    const dbPath = join(dir, "test.db");
    const store = new SQLiteStore(dbPath);
    store.migrate(join(__dirname, "..", "migrations"));
    enqueueTask(store, "my_task", { initialOutput: "Initial draft" });

    const runner = new TaskRunner({
      store,
      provider: makeMockProvider(),
      knowledgeRoot: join(dir, "knowledge"),
    });

    const result = await runner.runOnce();
    expect(result).not.toBeNull();
    expect(result!.status).toBe("completed");
    expect(result!.best_score).toBe(0.9);
    store.close();
  });
});
