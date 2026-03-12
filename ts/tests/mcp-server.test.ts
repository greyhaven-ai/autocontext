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
  // These are structural tests confirming the server builds correctly.
  // Integration tests should use the actual MCP client protocol.
});
