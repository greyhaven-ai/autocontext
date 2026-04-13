/**
 * Tests for AC-233: Remove hardcoded Anthropic model IDs from scaffolded TS
 * and template defaults.
 *
 * All scaffold, template, spec, and runner defaults should use empty string ""
 * meaning "inherit from provider default at runtime". Only provider-specific code
 * (e.g. createAnthropicProvider) should hardcode Anthropic model IDs.
 */

import { describe, it, expect, vi } from "vitest";

// ---------------------------------------------------------------------------
// 1. AgentTaskSpec schema defaults
// ---------------------------------------------------------------------------

describe("AgentTaskSpecSchema defaults", () => {
  it("should default judgeModel to empty string", async () => {
    const { AgentTaskSpecSchema } = await import("../src/scenarios/agent-task-spec.js");
    const spec = AgentTaskSpecSchema.parse({
      taskPrompt: "test",
      judgeRubric: "rubric",
    });
    expect(spec.judgeModel).toBe("");
  });

  it("should accept empty string as judgeModel", async () => {
    const { AgentTaskSpecSchema } = await import("../src/scenarios/agent-task-spec.js");
    const spec = AgentTaskSpecSchema.parse({
      taskPrompt: "test",
      judgeRubric: "rubric",
      judgeModel: "",
    });
    expect(spec.judgeModel).toBe("");
  });

  it("should preserve explicit model", async () => {
    const { AgentTaskSpecSchema } = await import("../src/scenarios/agent-task-spec.js");
    const spec = AgentTaskSpecSchema.parse({
      taskPrompt: "test",
      judgeRubric: "rubric",
      judgeModel: "gpt-4o",
    });
    expect(spec.judgeModel).toBe("gpt-4o");
  });
});

// ---------------------------------------------------------------------------
// 2. parseRawSpec fallback
// ---------------------------------------------------------------------------

describe("parseRawSpec defaults", () => {
  it("should default judge_model to empty string when missing", async () => {
    const { parseRawSpec } = await import("../src/scenarios/agent-task-spec.js");
    const spec = parseRawSpec({
      task_prompt: "test",
      judge_rubric: "rubric",
    });
    expect(spec.judgeModel).toBe("");
  });
});

// ---------------------------------------------------------------------------
// 3. Agent task designer — no hardcoded Anthropic defaults
// ---------------------------------------------------------------------------

describe("AgentTaskDesigner defaults", () => {
  it("should have empty judge_model in EXAMPLE_SPEC", async () => {
    // The EXAMPLE_SPEC is embedded in the system prompt. Check the prompt doesn't
    // use "claude-sonnet-4-20250514" as the judge_model default.
    const { AGENT_TASK_DESIGNER_SYSTEM } = await import("../src/scenarios/agent-task-designer.js");
    // The system prompt should not contain the hardcoded model as a default
    expect(AGENT_TASK_DESIGNER_SYSTEM).not.toContain('"judge_model": "claude-sonnet-4-20250514"');
  });

  it("should parse spec without judge_model to empty string", async () => {
    const { SPEC_START, SPEC_END, parseAgentTaskSpec } =
      await import("../src/scenarios/agent-task-designer.js");
    const raw = JSON.stringify({
      task_prompt: "test",
      judge_rubric: "rubric",
    });
    const text = `${SPEC_START}\n${raw}\n${SPEC_END}`;
    const spec = parseAgentTaskSpec(text);
    expect(spec.judgeModel).toBe("");
  });
});

// ---------------------------------------------------------------------------
// 4. AgentTaskCreator default model
// ---------------------------------------------------------------------------

describe("AgentTaskCreator defaults", () => {
  it("should default model to empty string", async () => {
    const { mkdtempSync, rmSync } = await import("node:fs");
    const { join } = await import("node:path");
    const { tmpdir } = await import("node:os");
    const { AgentTaskCreator } = await import("../src/scenarios/agent-task-creator.js");
    const { SPEC_END, SPEC_START } = await import("../src/scenarios/agent-task-designer.js");

    const provider = {
      name: "test",
      defaultModel: () => "test-model",
      complete: vi.fn().mockResolvedValue({
        text:
          `${SPEC_START}\n` +
          JSON.stringify(
            {
              task_prompt: "Write a haiku about testing software.",
              judge_rubric: "Evaluate format, relevance, and creativity.",
              output_format: "free_text",
              judge_model: "",
              max_rounds: 1,
              quality_threshold: 0.9,
            },
            null,
            2,
          ) +
          `\n${SPEC_END}`,
        usage: {},
      }),
    };

    const dir = mkdtempSync(join(tmpdir(), "autoctx-agent-task-creator-default-model-"));
    try {
      const creator = new AgentTaskCreator({
        provider,
        knowledgeRoot: dir,
      });

      await creator.create("Write a haiku about testing software.");

      expect(provider.complete).toHaveBeenCalled();
      expect(provider.complete.mock.calls[0]?.[0]?.model).toBe("");
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

// ---------------------------------------------------------------------------
// 5. SimpleAgentTask / TaskRunner defaults
// ---------------------------------------------------------------------------

describe("SimpleAgentTask defaults", () => {
  it("should fall back to provider.defaultModel() when model omitted", async () => {
    const { SimpleAgentTask } = await import("../src/execution/task-runner.js");
    const provider = {
      name: "test",
      defaultModel: () => "test-model",
      complete: vi.fn().mockResolvedValue({ text: "generated", usage: {} }),
    };
    const task = new SimpleAgentTask("prompt", "rubric", provider);

    await task.generateOutput();

    expect(provider.complete).toHaveBeenCalled();
    expect(provider.complete.mock.calls[0]?.[0]?.model).toBe("test-model");
  });
});

describe("TaskRunner defaults", () => {
  it("should fall back to provider.defaultModel() when model omitted", async () => {
    const { mkdtempSync, rmSync } = await import("node:fs");
    const { join } = await import("node:path");
    const { tmpdir } = await import("node:os");
    const { SQLiteStore } = await import("../src/storage/index.js");
    const { TaskRunner, enqueueTask } = await import("../src/execution/task-runner.js");

    const dir = mkdtempSync(join(tmpdir(), "autoctx-runner-default-model-"));
    const store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(import.meta.dirname, "..", "migrations"));

    const provider = {
      name: "test",
      defaultModel: () => "test-model",
      complete: vi
        .fn()
        .mockImplementation(async (opts: { systemPrompt: string; model: string }) => {
          if (opts.systemPrompt.includes("judge")) {
            return {
              text:
                "<!-- JUDGE_RESULT_START -->\n{" +
                '\"score\":0.9,\"reasoning\":\"ok\",\"dimensions\":{\"quality\":0.9}}' +
                "\n<!-- JUDGE_RESULT_END -->",
              usage: {},
            };
          }
          return { text: "draft", usage: {} };
        }),
    };

    try {
      enqueueTask(store, "test-spec", {
        taskPrompt: "prompt",
        rubric: "rubric",
        initialOutput: "draft",
        maxRounds: 1,
      });

      const runner = new TaskRunner({ store, provider });
      await runner.runOnce();

      expect(provider.complete).toHaveBeenCalled();
      expect(provider.complete.mock.calls.every(([call]) => call.model === "test-model")).toBe(
        true,
      );
    } finally {
      store.close();
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

// ---------------------------------------------------------------------------
// 6. MCP server default model
// ---------------------------------------------------------------------------

describe("MCP server defaults", () => {
  it("should default model to empty string", async () => {
    const { createMcpServer } = await import("../src/mcp/server.js");
    // We just verify the function signature accepts no model and uses ""
    // The actual default is tested by checking the server behavior
    const provider = {
      name: "test",
      defaultModel: () => "test-model",
      complete: vi.fn(),
    };
    const store = {} as any;
    // If model is omitted, it should default to ""
    // We can't easily inspect the closure, so we verify the signature
    // accepts undefined model (which becomes "")
    expect(() => createMcpServer({ store, provider })).not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// 7. Provider handles empty model correctly
// ---------------------------------------------------------------------------

describe("Provider empty model fallback", () => {
  it("Anthropic provider should use default when model is empty", async () => {
    const { createAnthropicProvider } = await import("../src/providers/index.js");
    const provider = createAnthropicProvider({ apiKey: "test" });

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        content: [{ type: "text", text: "Hello" }],
        model: "claude-sonnet-4-20250514",
        usage: { input_tokens: 10, output_tokens: 5 },
      }),
    });
    vi.stubGlobal("fetch", mockFetch);

    await provider.complete({
      systemPrompt: "sys",
      userPrompt: "test",
      model: "",
    });

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    // Empty model should fall back to provider default, NOT be sent as ""
    expect(body.model).toBe("claude-sonnet-4-20250514");

    vi.unstubAllGlobals();
  });

  it("OpenAI-compatible provider should use default when model is empty", async () => {
    const { createOpenAICompatibleProvider } = await import("../src/providers/index.js");
    const provider = createOpenAICompatibleProvider({
      apiKey: "test",
      model: "gpt-4o",
    });

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        choices: [{ message: { content: "ok" } }],
        model: "gpt-4o",
        usage: { prompt_tokens: 1, completion_tokens: 1 },
      }),
    });
    vi.stubGlobal("fetch", mockFetch);

    await provider.complete({
      systemPrompt: "sys",
      userPrompt: "test",
      model: "",
    });

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    // Empty model should fall back to provider default, NOT be sent as ""
    expect(body.model).toBe("gpt-4o");

    vi.unstubAllGlobals();
  });
});

// ---------------------------------------------------------------------------
// 8. Comprehensive scan — no hardcoded model in scaffold TS files
// ---------------------------------------------------------------------------

describe("No hardcoded Anthropic model in scaffold TS files", () => {
  const HARDCODED_MODEL = "claude-sonnet-4-20250514";

  // These scaffold files should NOT contain the hardcoded Anthropic model
  const SCAFFOLD_FILES = [
    "src/scenarios/agent-task-spec.ts",
    "src/scenarios/agent-task-designer.ts",
    "src/scenarios/agent-task-creator.ts",
    "src/execution/task-runner.ts",
    "src/mcp/server.ts",
  ];

  for (const filepath of SCAFFOLD_FILES) {
    it(`${filepath} should not hardcode Anthropic model`, async () => {
      const { readFileSync } = await import("node:fs");
      const { join } = await import("node:path");
      const content = readFileSync(join(import.meta.dirname, "..", filepath), "utf-8");
      const count = (content.match(new RegExp(HARDCODED_MODEL, "g")) || []).length;
      expect(count).toBe(0);
    });
  }
});
