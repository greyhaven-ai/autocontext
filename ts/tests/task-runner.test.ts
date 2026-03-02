import { describe, it, expect, beforeEach } from "vitest";
import { mkdtempSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { SQLiteStore } from "../src/storage/index.js";
import { TaskRunner, SimpleAgentTask, enqueueTask } from "../src/execution/task-runner.js";
import type { LLMProvider, CompletionResult } from "../src/types/index.js";

const MIGRATIONS_DIR = join(import.meta.dirname, "..", "migrations");

function createStore(): SQLiteStore {
  const dir = mkdtempSync(join(tmpdir(), "mts-runner-"));
  const store = new SQLiteStore(join(dir, "test.db"));
  store.migrate(MIGRATIONS_DIR);
  return store;
}

function makeMockProvider(response = "mock output"): LLMProvider {
  let calls = 0;
  return {
    name: "mock",
    defaultModel: () => "mock",
    complete: async (opts) => {
      calls++;
      // If it's a judge call, return structured response
      if (opts.systemPrompt.includes("judge")) {
        return {
          text: `<!-- JUDGE_RESULT_START -->\n{"score": 0.9, "reasoning": "Good", "dimensions": {"quality": 0.9}}\n<!-- JUDGE_RESULT_END -->`,
          usage: {},
        };
      }
      return { text: response, usage: {} };
    },
  };
}

describe("enqueueTask", () => {
  it("creates task with UUID", () => {
    const store = createStore();
    const id = enqueueTask(store, "test-spec", { taskPrompt: "Do something" });
    expect(id).toMatch(/^[0-9a-f-]{36}$/);
    expect(store.pendingTaskCount()).toBe(1);
  });

  it("sets priority", () => {
    const store = createStore();
    enqueueTask(store, "low", { priority: 1 });
    enqueueTask(store, "high", { priority: 10 });
    const task = store.dequeueTask();
    expect(task!.spec_name).toBe("high");
  });
});

describe("TaskRunner", () => {
  it("processes a task end-to-end", async () => {
    const store = createStore();
    enqueueTask(store, "test-spec", {
      taskPrompt: "Write a greeting",
      rubric: "Be friendly",
      initialOutput: "Hello!",
    });

    const runner = new TaskRunner({
      store,
      provider: makeMockProvider(),
    });

    const result = await runner.runOnce();
    expect(result).not.toBeNull();
    expect(result!.status).toBe("completed");
    expect(result!.best_score).toBe(0.9);
    expect(runner.tasksProcessed).toBe(1);
  });

  it("returns null on empty queue", async () => {
    const store = createStore();
    const runner = new TaskRunner({ store, provider: makeMockProvider() });
    expect(await runner.runOnce()).toBeNull();
  });

  it("handles provider errors gracefully", async () => {
    const store = createStore();
    enqueueTask(store, "fail-spec", { initialOutput: "test" });

    const failProvider: LLMProvider = {
      name: "fail",
      defaultModel: () => "m",
      complete: async () => {
        throw new Error("API down");
      },
    };

    const runner = new TaskRunner({ store, provider: failProvider });
    const result = await runner.runOnce();
    expect(result).not.toBeNull();
    expect(result!.status).toBe("failed");
    expect(result!.error).toContain("API down");
    // Verify no stack trace in error (only message stored)
    expect(result!.error).not.toContain("\n    at ");
  });

  it("rejects invalid config via Zod validation", async () => {
    const store = createStore();
    const provider = makeMockProvider();
    // max_rounds must be a positive integer, not a string
    store.enqueueTask("bad", "test_spec", 0, { max_rounds: "not_a_number" });
    const runner = new TaskRunner({ store, provider });
    const result = await runner.runOnce();
    expect(result!.status).toBe("failed");
    expect(result!.error).toContain("Expected number");
  });
});

describe("SimpleAgentTask", () => {
  it("generates and revises output", async () => {
    const provider = makeMockProvider("generated text");
    const task = new SimpleAgentTask("Write something", "Be good", provider);
    const output = await task.generateOutput();
    expect(output).toBe("generated text");

    const revised = await task.reviseOutput(
      output,
      { score: 0.5, reasoning: "Needs work", dimensionScores: {} },
      {},
    );
    expect(revised).toBe("generated text"); // Mock returns same for non-judge calls
  });
});
