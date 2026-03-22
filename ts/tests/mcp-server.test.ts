import { describe, it, expect, beforeEach } from "vitest";
import { mkdtempSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { createMcpServer } from "../src/mcp/server.js";
import { SQLiteStore } from "../src/storage/index.js";
import type { LLMProvider } from "../src/types/index.js";

const MIGRATIONS_DIR = join(import.meta.dirname, "..", "migrations");

function createStore(): SQLiteStore {
  const dir = mkdtempSync(join(tmpdir(), "autocontext-mcp-"));
  const store = new SQLiteStore(join(dir, "test.db"));
  store.migrate(MIGRATIONS_DIR);
  return store;
}

function makeMockProvider(): LLMProvider {
  return {
    name: "mock",
    defaultModel: () => "mock",
    complete: async (opts) => {
      if (opts.systemPrompt.includes("judge")) {
        return {
          text: '<!-- JUDGE_RESULT_START -->\n{"score": 0.85, "reasoning": "Good work", "dimensions": {"quality": 0.9}}\n<!-- JUDGE_RESULT_END -->',
          usage: {},
        };
      }
      return { text: "generated output", usage: {} };
    },
  };
}

function makeRlmProvider(): LLMProvider {
  return {
    name: "rlm-mock",
    defaultModel: () => "mock",
    complete: async (opts) => {
      if (opts.systemPrompt.includes("REPL-loop mode")) {
        return {
          text: '<code>answer.ready = true;\nanswer.content = "hello from MCP RLM";</code>',
          usage: {},
        };
      }
      if (opts.systemPrompt.includes("judge")) {
        return {
          text: '<!-- JUDGE_RESULT_START -->\n{"score": 0.85, "reasoning": "Good work", "dimensions": {"quality": 0.9}}\n<!-- JUDGE_RESULT_END -->',
          usage: {},
        };
      }
      return { text: "generated output", usage: {} };
    },
  };
}

describe("createMcpServer", () => {
  it("creates a server with tools", () => {
    const store = createStore();
    const server = createMcpServer({
      store,
      provider: makeMockProvider(),
    });
    expect(server).toBeDefined();
  });

  // Note: Full tool invocation tests would require MCP client setup.
  // These tests use the SDK's registered tool handlers directly.

  it("registers a direct REPL session tool that returns shared RLM output", async () => {
    const store = createStore();
    const server = createMcpServer({
      store,
      provider: makeRlmProvider(),
    }) as unknown as {
      _registeredTools: Record<string, { handler: (args: Record<string, unknown>, extra: unknown) => Promise<{ content: Array<{ text: string }> }> }>;
    };

    const tool = server._registeredTools.run_repl_session;
    expect(tool).toBeDefined();

    const result = await tool.handler({
      taskPrompt: "Explain testing.",
      rubric: "Be clear.",
      phase: "generate",
      rlmMaxTurns: 2,
    }, {});

    const payload = JSON.parse(result.content[0].text);
    expect(payload.content).toBe("hello from MCP RLM");
    expect(payload.phase).toBe("generate");
    expect(payload.backend).toBe("secure_exec");
  });

  it("returns a structured error when revise phase omits current output", async () => {
    const store = createStore();
    const server = createMcpServer({
      store,
      provider: makeRlmProvider(),
    }) as unknown as {
      _registeredTools: Record<string, { handler: (args: Record<string, unknown>, extra: unknown) => Promise<{ content: Array<{ text: string }> }> }>;
    };

    const result = await server._registeredTools.run_repl_session.handler({
      taskPrompt: "Explain testing.",
      rubric: "Be clear.",
      phase: "revise",
    }, {});

    const payload = JSON.parse(result.content[0].text);
    expect(payload.error).toContain("currentOutput");
  });
});
