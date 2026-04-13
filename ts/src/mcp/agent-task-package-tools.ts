import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { join } from "node:path";
import { z } from "zod";
import { AgentTaskStore } from "../scenarios/agent-task-store.js";
import { ArtifactStore } from "../knowledge/artifact-store.js";
import {
  exportStrategyPackage,
  importStrategyPackage,
  type ConflictPolicy,
} from "../knowledge/package.js";
import type { SQLiteStore } from "../storage/index.js";
import type { LLMProvider } from "../types/index.js";

interface AgentTaskPackageToolResult {
  [key: string]: unknown;
  content: Array<{ type: "text"; text: string }>;
}

interface AgentTaskPackageToolServer {
  tool(
    name: string,
    description: string,
    schema: Record<string, z.ZodTypeAny>,
    handler: (args: Record<string, unknown>) =>
      | Promise<AgentTaskPackageToolResult>
      | AgentTaskPackageToolResult,
  ): unknown;
}

type AgentTaskPackageToolRegistrar = McpServer | AgentTaskPackageToolServer;

interface AgentTaskPackageToolInternals {
  createAgentTaskStore(root: string): AgentTaskStore;
  createArtifactStore(opts: { runsRoot: string; knowledgeRoot: string }): ArtifactStore;
  exportStrategyPackage: typeof exportStrategyPackage;
  importStrategyPackage: typeof importStrategyPackage;
}

const defaultInternals: AgentTaskPackageToolInternals = {
  createAgentTaskStore: (root) => new AgentTaskStore(root),
  createArtifactStore: (opts) => new ArtifactStore(opts),
  exportStrategyPackage,
  importStrategyPackage,
};

function jsonContent(payload: unknown): AgentTaskPackageToolResult {
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

function normalizeConflictPolicy(value: unknown): ConflictPolicy {
  if (value === "overwrite" || value === "skip") {
    return value;
  }
  return "merge";
}

function registerTool(
  server: AgentTaskPackageToolRegistrar,
  name: string,
  description: string,
  schema: Record<string, z.ZodTypeAny>,
  handler: (args: Record<string, unknown>) =>
    | Promise<AgentTaskPackageToolResult>
    | AgentTaskPackageToolResult,
): void {
  if (server instanceof McpServer) {
    server.tool(name, description, schema, handler);
    return;
  }
  server.tool(name, description, schema, handler);
}

export function buildAgentTaskNotFoundPayload(): Record<string, string> {
  return { error: "Task not found" };
}

export function registerAgentTaskPackageTools(
  server: AgentTaskPackageToolRegistrar,
  opts: {
    provider: LLMProvider;
    store: SQLiteStore;
    runsRoot: string;
    knowledgeRoot: string;
    skillsRoot: string;
    internals?: Partial<AgentTaskPackageToolInternals>;
  },
): void {
  const internals = { ...defaultInternals, ...opts.internals };
  const taskStoreRoot = join(opts.knowledgeRoot, "_agent_tasks");

  registerTool(
    server,
    "create_agent_task",
    "Create a named agent task spec for evaluation",
    {
      name: z.string(),
      taskPrompt: z.string(),
      rubric: z.string(),
      referenceContext: z.string().optional(),
    },
    async (args) => {
      const taskStore = internals.createAgentTaskStore(taskStoreRoot);
      taskStore.create({
        name: String(args.name),
        taskPrompt: String(args.taskPrompt),
        rubric: String(args.rubric),
        referenceContext: typeof args.referenceContext === "string"
          ? args.referenceContext
          : undefined,
      });
      return jsonContent({ name: args.name, created: true });
    },
  );

  registerTool(
    server,
    "list_agent_tasks",
    "List created agent task specs",
    {},
    async () => jsonContent(internals.createAgentTaskStore(taskStoreRoot).list()),
  );

  registerTool(
    server,
    "get_agent_task",
    "Get a specific agent task spec by name",
    { name: z.string() },
    async (args) => {
      const task = internals.createAgentTaskStore(taskStoreRoot).get(String(args.name));
      return jsonContent(task ?? buildAgentTaskNotFoundPayload());
    },
  );

  registerTool(
    server,
    "generate_output",
    "Generate an initial agent output for a task prompt",
    { taskPrompt: z.string(), systemPrompt: z.string().default("") },
    async (args) => {
      const result = await opts.provider.complete({
        systemPrompt: String(args.systemPrompt ?? ""),
        userPrompt: String(args.taskPrompt),
      });
      return jsonContent({ output: result.text, model: result.model });
    },
  );

  registerTool(
    server,
    "export_package",
    "Export a versioned strategy package for a scenario",
    { scenario: z.string() },
    async (args) => {
      const artifacts = internals.createArtifactStore({
        runsRoot: opts.runsRoot,
        knowledgeRoot: opts.knowledgeRoot,
      });
      return jsonContent(internals.exportStrategyPackage({
        scenarioName: String(args.scenario),
        artifacts,
        store: opts.store,
      }));
    },
  );

  registerTool(
    server,
    "import_package",
    "Import a strategy package into scenario knowledge",
    { packageData: z.string(), conflictPolicy: z.string().default("merge") },
    async (args) => {
      const artifacts = internals.createArtifactStore({
        runsRoot: opts.runsRoot,
        knowledgeRoot: opts.knowledgeRoot,
      });
      const parsedPackage: unknown = JSON.parse(String(args.packageData));
      const rawPackage = isRecord(parsedPackage) ? parsedPackage : {};
      return jsonContent(internals.importStrategyPackage({
        rawPackage,
        artifacts,
        skillsRoot: opts.skillsRoot,
        conflictPolicy: normalizeConflictPolicy(args.conflictPolicy),
      }));
    },
  );
}
