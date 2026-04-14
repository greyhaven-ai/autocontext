import { describe, expect, it } from "vitest";

import {
  buildMcpServeRequest,
  MCP_SERVE_HELP_TEXT,
} from "../src/cli/mcp-serve-command-workflow.js";

describe("mcp-serve command workflow", () => {
  it("exposes stable help text", () => {
    expect(MCP_SERVE_HELP_TEXT).toContain("autoctx mcp-serve");
    expect(MCP_SERVE_HELP_TEXT).toContain("evaluate_output");
    expect(MCP_SERVE_HELP_TEXT).toContain("run_improvement_loop");
    expect(MCP_SERVE_HELP_TEXT).toContain("queue_task");
    expect(MCP_SERVE_HELP_TEXT.toLowerCase()).toContain("stdio");
    expect(MCP_SERVE_HELP_TEXT.toLowerCase()).toContain("see also");
  });

  it("builds MCP serve startup requests", () => {
    expect(
      buildMcpServeRequest({
        store: { kind: "sqlite" },
        provider: { name: "deterministic" },
        model: "fixture-model",
        dbPath: "/tmp/autocontext.sqlite3",
        runsRoot: "/tmp/runs",
        knowledgeRoot: "/tmp/knowledge",
      }),
    ).toEqual({
      store: { kind: "sqlite" },
      provider: { name: "deterministic" },
      model: "fixture-model",
      dbPath: "/tmp/autocontext.sqlite3",
      runsRoot: "/tmp/runs",
      knowledgeRoot: "/tmp/knowledge",
    });
  });
});
