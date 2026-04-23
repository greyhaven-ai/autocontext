import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { registerProductionTracesTools } from "../src/mcp/production-traces-tools.js";
import { runProductionTracesCommand } from "../src/production-traces/cli/index.js";
import { newProductionTraceId } from "../src/production-traces/contract/branded-ids.js";
import {
  makeTrace,
  TEST_DATE,
  writeIncomingBatch,
} from "./control-plane/production-traces/cli/_helpers/fixtures.js";

function createFakeServer() {
  const registeredTools: Record<
    string,
    {
      description: string;
      schema: Record<string, unknown>;
      handler: (args: Record<string, unknown>) => Promise<{ content: Array<{ type: string; text: string }> }>;
    }
  > = {};

  return {
    registeredTools,
    tool(
      name: string,
      description: string,
      schema: Record<string, unknown>,
      handler: (args: Record<string, unknown>) => Promise<{ content: Array<{ type: string; text: string }> }>,
    ) {
      registeredTools[name] = { description, schema, handler };
    },
  };
}

let cwd: string;

beforeEach(() => {
  cwd = mkdtempSync(join(tmpdir(), "autocontext-pt-mcp-"));
});

afterEach(() => {
  rmSync(cwd, { recursive: true, force: true });
});

describe("production-traces MCP tools", () => {
  test("build-dataset forwards provider/app/env/outcome filters to the CLI", async () => {
    await runProductionTracesCommand(["init"], { cwd });

    const base = Date.parse("2026-04-17T12:00:00.000Z");
    const traces = [
      makeTrace({
        traceId: newProductionTraceId(),
        startedAt: new Date(base).toISOString(),
        provider: { name: "openai" },
        env: { environmentTag: "production" as any, appId: "target-app" as any, taskType: "chat" },
        outcome: { label: "success" },
      }),
      makeTrace({
        traceId: newProductionTraceId(),
        startedAt: new Date(base + 60_000).toISOString(),
        provider: { name: "anthropic" },
        env: { environmentTag: "production" as any, appId: "target-app" as any, taskType: "chat" },
        outcome: { label: "success" },
      }),
      makeTrace({
        traceId: newProductionTraceId(),
        startedAt: new Date(base + 120_000).toISOString(),
        provider: { name: "anthropic" },
        env: { environmentTag: "production" as any, appId: "other-app" as any, taskType: "chat" },
        outcome: { label: "success" },
      }),
      makeTrace({
        traceId: newProductionTraceId(),
        startedAt: new Date(base + 180_000).toISOString(),
        provider: { name: "anthropic" },
        env: { environmentTag: "staging" as any, appId: "target-app" as any, taskType: "chat" },
        outcome: { label: "failure" },
      }),
    ];
    writeIncomingBatch(cwd, TEST_DATE, "mcp-filter-batch", traces);
    const ingest = await runProductionTracesCommand(["ingest"], { cwd });
    expect(ingest.exitCode).toBe(0);

    const server = createFakeServer();
    registerProductionTracesTools(server);
    const tool = server.registeredTools.production_traces_build_dataset;
    expect(tool).toBeDefined();
    expect(Object.keys(tool!.schema)).toEqual(
      expect.arrayContaining(["provider", "app", "env", "outcome"]),
    );

    const result = await tool!.handler({
      cwd,
      name: "anthropic-target-success",
      provider: "anthropic",
      app: "target-app",
      env: "production",
      outcome: "success",
      clusterStrategy: "taskType",
      allowSyntheticRubrics: true,
    });
    const envelope = JSON.parse(result.content[0]!.text) as {
      stdout: string;
      stderr: string;
      exitCode: number;
    };
    expect(envelope.stderr).toBe("");
    expect(envelope.exitCode).toBe(0);
    const dataset = JSON.parse(envelope.stdout) as { stats: { traceCount: number } };
    expect(dataset.stats.traceCount).toBe(1);
  });
});
