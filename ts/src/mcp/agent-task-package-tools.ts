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

interface JsonToolResponse {
  content: Array<{
    type: "text";
    text: string;
  }>;
}

type McpToolRegistrar = {
  tool: (...args: any[]) => unknown;
};

type AgentTaskStoreLike = Pick<AgentTaskStore, "create" | "list" | "get">;
type ArtifactStoreLike = ArtifactStore;

interface AgentTaskPackageInternals {
  createAgentTaskStore(dir: string): AgentTaskStoreLike;
  createArtifactStore(opts: { runsRoot: string; knowledgeRoot: string }): ArtifactStoreLike;
  exportStrategyPackage(opts: {
    scenarioName: string;
    artifacts: ArtifactStore;
    store: SQLiteStore;
  }): Record<string, unknown>;
  importStrategyPackage(opts: {
    rawPackage: Record<string, unknown>;
    artifacts: ArtifactStore;
    skillsRoot: string;
    conflictPolicy?: ConflictPolicy;
  }): object;
}

const defaultInternals: AgentTaskPackageInternals = {
  createAgentTaskStore: (dir) => new AgentTaskStore(dir),
  createArtifactStore: (opts) => new ArtifactStore(opts),
  exportStrategyPackage: (opts) => exportStrategyPackage(opts),
  importStrategyPackage: (opts) => importStrategyPackage(opts),
};

function jsonText(payload: unknown, indent?: number): JsonToolResponse {
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify(payload, null, indent),
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

export function buildAgentTaskNotFoundPayload(): { error: string } {
  return { error: "Task not found" };
}

export function registerAgentTaskPackageTools(
  server: McpToolRegistrar,
  opts: {
    provider: Pick<LLMProvider, "complete">;
    store: SQLiteStore;
    runsRoot: string;
    knowledgeRoot: string;
    skillsRoot: string;
    internals?: Partial<AgentTaskPackageInternals>;
  },
): void {
  const internals: AgentTaskPackageInternals = {
    ...defaultInternals,
    ...opts.internals,
  };

  const taskStoreDir = join(opts.knowledgeRoot, "_agent_tasks");

  server.tool(
    "create_agent_task",
    "Create a named agent task spec for evaluation",
    {
      name: z.string(),
      taskPrompt: z.string(),
      rubric: z.string(),
      referenceContext: z.string().optional(),
    },
    async (args: Record<string, unknown>) => {
      const taskStore = internals.createAgentTaskStore(taskStoreDir);
      taskStore.create({
        name: String(args.name),
        taskPrompt: String(args.taskPrompt),
        rubric: String(args.rubric),
        referenceContext: typeof args.referenceContext === "string"
          ? args.referenceContext
          : undefined,
      });
      return jsonText({ name: args.name, created: true });
    },
  );

  server.tool(
    "list_agent_tasks",
    "List created agent task specs",
    {},
    async () => {
      const taskStore = internals.createAgentTaskStore(taskStoreDir);
      return jsonText(taskStore.list(), 2);
    },
  );

  server.tool(
    "get_agent_task",
    "Get a specific agent task spec by name",
    { name: z.string() },
    async (args: Record<string, unknown>) => {
      const taskStore = internals.createAgentTaskStore(taskStoreDir);
      const task = taskStore.get(String(args.name));
      return jsonText(task ?? buildAgentTaskNotFoundPayload(), task ? 2 : undefined);
    },
  );

  server.tool(
    "generate_output",
    "Generate an initial agent output for a task prompt",
    { taskPrompt: z.string(), systemPrompt: z.string().default("") },
    async (args: Record<string, unknown>) => {
      const result = await opts.provider.complete({
        systemPrompt: String(args.systemPrompt ?? ""),
        userPrompt: String(args.taskPrompt),
      });
      return jsonText({ output: result.text, model: result.model });
    },
  );

  server.tool(
    "export_package",
    "Export a versioned strategy package for a scenario",
    { scenario: z.string() },
    async (args: Record<string, unknown>) => {
      const artifacts = internals.createArtifactStore({
        runsRoot: opts.runsRoot,
        knowledgeRoot: opts.knowledgeRoot,
      });
      return jsonText(
        internals.exportStrategyPackage({
          scenarioName: String(args.scenario),
          artifacts,
          store: opts.store,
        }),
        2,
      );
    },
  );

  server.tool(
    "import_package",
    "Import a strategy package into scenario knowledge",
    { packageData: z.string(), conflictPolicy: z.string().default("merge") },
    async (args: Record<string, unknown>) => {
      const artifacts = internals.createArtifactStore({
        runsRoot: opts.runsRoot,
        knowledgeRoot: opts.knowledgeRoot,
      });
      const parsedPackage: unknown = JSON.parse(String(args.packageData));
      return jsonText(
        internals.importStrategyPackage({
          rawPackage: isRecord(parsedPackage) ? parsedPackage : {},
          artifacts,
          skillsRoot: opts.skillsRoot,
          conflictPolicy: normalizeConflictPolicy(args.conflictPolicy),
        }),
        2,
      );
    },
  );
}
