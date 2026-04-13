import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { reviseSpec } from "../scenarios/scenario-revision.js";
import type { LLMProvider } from "../types/index.js";

interface ScenarioRevisionToolResult {
  [key: string]: unknown;
  content: Array<{ type: "text"; text: string }>;
}

interface ScenarioRevisionToolServer {
  tool(
    name: string,
    description: string,
    schema: Record<string, z.ZodTypeAny>,
    handler: (args: Record<string, unknown>) =>
      | Promise<ScenarioRevisionToolResult>
      | ScenarioRevisionToolResult,
  ): unknown;
}

type ScenarioRevisionToolRegistrar = McpServer | ScenarioRevisionToolServer;

interface ScenarioRevisionToolInternals {
  reviseSpec: typeof reviseSpec;
}

function jsonContent(payload: unknown): ScenarioRevisionToolResult {
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify(payload, null, 2),
      },
    ],
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function registerTool(
  server: ScenarioRevisionToolRegistrar,
  name: string,
  description: string,
  schema: Record<string, z.ZodTypeAny>,
  handler: (args: Record<string, unknown>) =>
    | Promise<ScenarioRevisionToolResult>
    | ScenarioRevisionToolResult,
): void {
  if (server instanceof McpServer) {
    server.tool(name, description, schema, handler);
    return;
  }
  server.tool(name, description, schema, handler);
}

export function registerScenarioRevisionTools(
  server: ScenarioRevisionToolRegistrar,
  opts: {
    provider: LLMProvider;
    internals?: Partial<ScenarioRevisionToolInternals>;
  },
): void {
  const internals = { reviseSpec, ...opts.internals };

  registerTool(
    server,
    "revise_scenario",
    "Revise a scenario spec based on user feedback. Takes the current spec and feedback, returns an updated spec via LLM.",
    {
      currentSpec: z.record(z.unknown()),
      feedback: z.string(),
      family: z.string().default("agent_task"),
    },
    async (args) => {
      const result = await internals.reviseSpec({
        currentSpec: isRecord(args.currentSpec) ? args.currentSpec : {},
        feedback: String(args.feedback),
        family: String(args.family ?? "agent_task"),
        provider: opts.provider,
      });
      return jsonContent({
        changesApplied: result.changesApplied,
        revised: result.revised,
        error: result.error ?? null,
      });
    },
  );
}
