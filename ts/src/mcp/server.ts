/**
 * MCP server for autocontext — expanded package control plane.
 * Covers evaluation, scenarios, runs, knowledge, feedback, and exports.
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import type { LLMProvider } from "../types/index.js";
import { SandboxManager } from "../execution/sandbox.js";
import { SQLiteStore } from "../storage/index.js";
import { loadSettings } from "../config/index.js";
import { SolveManager } from "../knowledge/solver.js";
import { registerCampaignTools } from "./campaign-tools.js";
import { registerCoreControlPlaneTools } from "./core-control-tools.js";
import { registerAgentTaskPackageTools } from "./agent-task-package-tools.js";
import { registerFeedbackReplayTools } from "./feedback-replay-tools.js";
import { registerKnowledgeReadbackTools } from "./knowledge-readback-tools.js";
import { registerMissionTools } from "./mission-tools.js";
import { registerRunManagementTools } from "./run-management-tools.js";
import { registerSandboxTools } from "./sandbox-tools.js";
import { registerScenarioCatalogTools } from "./scenario-catalog-tools.js";
import { registerScenarioExecutionTools } from "./scenario-execution-tools.js";
import { registerScenarioRevisionTools } from "./scenario-revision-tools.js";
import { registerSolveTools } from "./solve-tools.js";
import { registerProductionTracesTools } from "./production-traces-tools.js";

export interface MtsServerOpts {
  store: SQLiteStore;
  provider: LLMProvider;
  model?: string;
  /** SQLite DB path for mission control helpers */
  dbPath?: string;
  /** Directory for agent task spec JSON files */
  tasksDir?: string;
  /** Root directory for run artifacts */
  runsRoot?: string;
  /** Root directory for knowledge artifacts */
  knowledgeRoot?: string;
}

export function resolveMcpArtifactRoots(opts: Pick<MtsServerOpts, "runsRoot" | "knowledgeRoot">): {
  runsRoot: string;
  knowledgeRoot: string;
} {
  const settings = loadSettings();
  return {
    runsRoot: opts.runsRoot ?? settings.runsRoot,
    knowledgeRoot: opts.knowledgeRoot ?? settings.knowledgeRoot,
  };
}

export function createMcpServer(opts: MtsServerOpts): McpServer {
  const { store, provider, model = "" } = opts;
  const settings = loadSettings();
  const { runsRoot, knowledgeRoot } = resolveMcpArtifactRoots(opts);
  const server = new McpServer({
    name: "autocontext",
    version: "0.2.3",
  });
  const solveManager = new SolveManager({ provider, store, runsRoot, knowledgeRoot });
  const sandboxManager = new SandboxManager({ provider, store, runsRoot, knowledgeRoot });

  registerCoreControlPlaneTools(server, {
    store,
    provider,
    model,
  });

  registerScenarioCatalogTools(server);

  registerScenarioRevisionTools(server, {
    provider,
  });

  registerRunManagementTools(server, {
    store,
    provider,
    runsRoot,
    knowledgeRoot,
    settings: {
      maxRetries: settings.maxRetries,
      backpressureMinDelta: settings.backpressureMinDelta,
      playbookMaxVersions: settings.playbookMaxVersions,
      contextBudgetTokens: settings.contextBudgetTokens,
      curatorEnabled: settings.curatorEnabled,
      curatorConsolidateEveryNGens: settings.curatorConsolidateEveryNGens,
      skillMaxLessons: settings.skillMaxLessons,
      deadEndTrackingEnabled: settings.deadEndTrackingEnabled,
      deadEndMaxEntries: settings.deadEndMaxEntries,
      stagnationResetEnabled: settings.stagnationResetEnabled,
      stagnationRollbackThreshold: settings.stagnationRollbackThreshold,
      stagnationPlateauWindow: settings.stagnationPlateauWindow,
      stagnationPlateauEpsilon: settings.stagnationPlateauEpsilon,
      stagnationDistillTopLessons: settings.stagnationDistillTopLessons,
      explorationMode: settings.explorationMode,
      notifyWebhookUrl: settings.notifyWebhookUrl,
      notifyOn: settings.notifyOn,
    },
  });

  registerScenarioExecutionTools(server);

  registerKnowledgeReadbackTools(server, {
    store,
    artifactExportStore: store,
    runsRoot,
    knowledgeRoot,
  });

  registerFeedbackReplayTools(server, {
    store,
    runsRoot,
  });

  registerSolveTools(server, {
    solveManager,
  });

  registerSandboxTools(server, {
    sandboxManager,
  });

  registerAgentTaskPackageTools(server, {
    provider,
    store,
    runsRoot,
    knowledgeRoot,
    skillsRoot: settings.skillsRoot,
  });

  registerMissionTools(server, {
    dbPath: opts.dbPath ?? settings.dbPath,
    runsRoot,
  });
  registerCampaignTools(server, {
    dbPath: opts.dbPath ?? settings.dbPath,
  });

  registerProductionTracesTools(server);

  return server;
}

/**
 * Start the MCP server on stdio.
 */
export async function startServer(opts: MtsServerOpts): Promise<void> {
  const server = createMcpServer(opts);
  const transport = new StdioServerTransport();
  await server.connect(transport);
}
