/**
 * MCP server for autocontext — agent task evaluation tools.
 * Port of autocontext/src/autocontext/mcp/tools.py (agent task subset).
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import type { LLMProvider } from "../types/index.js";
import { LLMJudge } from "../judge/index.js";
import { ImprovementLoop } from "../execution/improvement-loop.js";
import { enqueueTask } from "../execution/task-runner.js";
import { SQLiteStore } from "../storage/index.js";
import { SimpleAgentTask } from "../execution/task-runner.js";
import { loadSettings } from "../config/index.js";
import { runAgentTaskRlmSession } from "../rlm/index.js";

export interface MtsServerOpts {
  store: SQLiteStore;
  provider: LLMProvider;
  model?: string;
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
    version: "0.2.0",
  });

  // -- evaluate_output --
  server.tool(
    "evaluate_output",
    "One-shot evaluation of output against a rubric",
    {
      taskPrompt: z.string().describe("The task the agent was given"),
      agentOutput: z.string().describe("The agent's output to evaluate"),
      rubric: z.string().describe("Evaluation rubric"),
      referenceContext: z.string().optional().describe("Authoritative reference for fact-checking"),
      requiredConcepts: z.array(z.string()).optional().describe("Concepts the output must address"),
    },
    async (args) => {
      const judge = new LLMJudge({ provider, model, rubric: args.rubric });
      const result = await judge.evaluate({
        taskPrompt: args.taskPrompt,
        agentOutput: args.agentOutput,
        referenceContext: args.referenceContext,
        requiredConcepts: args.requiredConcepts,
      });
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(
              {
                score: result.score,
                reasoning: result.reasoning,
                dimensionScores: result.dimensionScores,
              },
              null,
              2,
            ),
          },
        ],
      };
    },
  );

  // -- run_improvement_loop --
  server.tool(
    "run_improvement_loop",
    "Run multi-round improvement loop on agent output",
    {
      taskPrompt: z.string().describe("The task prompt"),
      rubric: z.string().describe("Evaluation rubric"),
      initialOutput: z.string().optional().describe("Starting output to improve"),
      maxRounds: z.number().int().default(5).describe("Maximum improvement rounds"),
      qualityThreshold: z.number().default(0.9).describe("Score threshold to stop"),
      referenceContext: z.string().optional(),
      requiredConcepts: z.array(z.string()).optional(),
      rlmEnabled: z.boolean().optional().describe("Use REPL-loop mode for generation and revisions"),
      rlmModel: z.string().optional().describe("Optional model override for REPL-loop mode"),
      rlmMaxTurns: z.number().int().positive().optional(),
      rlmMaxTokensPerTurn: z.number().int().positive().optional(),
      rlmTemperature: z.number().min(0).max(2).optional(),
      rlmMaxStdoutChars: z.number().int().positive().optional(),
      rlmCodeTimeoutMs: z.number().int().positive().optional(),
      rlmMemoryLimitMb: z.number().int().positive().optional(),
    },
    async (args) => {
      const task = new SimpleAgentTask(
        args.taskPrompt,
        args.rubric,
        provider,
        model,
        undefined,
        {
          enabled: args.rlmEnabled ?? false,
          model: args.rlmModel,
          maxTurns: args.rlmMaxTurns,
          maxTokensPerTurn: args.rlmMaxTokensPerTurn,
          temperature: args.rlmTemperature,
          maxStdoutChars: args.rlmMaxStdoutChars,
          codeTimeoutMs: args.rlmCodeTimeoutMs,
          memoryLimitMb: args.rlmMemoryLimitMb,
        },
      );
      const initialOutput = args.initialOutput ?? await task.generateOutput({
        referenceContext: args.referenceContext,
        requiredConcepts: args.requiredConcepts,
      });
      const loop = new ImprovementLoop({
        task,
        maxRounds: args.maxRounds,
        qualityThreshold: args.qualityThreshold,
      });
      const result = await loop.run({
        initialOutput,
        state: {},
        referenceContext: args.referenceContext,
        requiredConcepts: args.requiredConcepts,
      });
      const rlmSessions = task.getRlmSessions();

      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(
              {
                totalRounds: result.totalRounds,
                metThreshold: result.metThreshold,
                bestScore: result.bestScore,
                bestRound: result.bestRound,
                judgeFailures: result.judgeFailures,
                rounds: result.rounds.map((r) => ({
                  round: r.roundNumber,
                  score: r.score,
                  isRevision: r.isRevision,
                  judgeFailed: r.judgeFailed,
                  reasoningPreview: r.reasoning.slice(0, 200),
                })),
                bestOutputPreview: result.bestOutput.slice(0, 500),
                ...(rlmSessions.length > 0 ? { rlmSessions } : {}),
              },
              null,
              2,
            ),
          },
        ],
      };
    },
  );

  // -- run_repl_session --
  server.tool(
    "run_repl_session",
    "Run a direct REPL-loop session for agent-task generation or revision",
    {
      taskPrompt: z.string().describe("The task prompt"),
      rubric: z.string().describe("Evaluation rubric"),
      phase: z.enum(["generate", "revise"]).default("generate"),
      currentOutput: z.string().optional().describe("Current output when revising"),
      referenceContext: z.string().optional(),
      requiredConcepts: z.array(z.string()).optional(),
      rlmModel: z.string().optional().describe("Optional model override for REPL-loop mode"),
      rlmMaxTurns: z.number().int().positive().optional(),
      rlmMaxTokensPerTurn: z.number().int().positive().optional(),
      rlmTemperature: z.number().min(0).max(2).optional(),
      rlmMaxStdoutChars: z.number().int().positive().optional(),
      rlmCodeTimeoutMs: z.number().int().positive().optional(),
      rlmMemoryLimitMb: z.number().int().positive().optional(),
    },
    async (args) => {
      if (args.phase === "revise" && !args.currentOutput) {
        return {
          content: [
            {
              type: "text" as const,
              text: JSON.stringify({
                error: "currentOutput is required when phase=revise",
              }, null, 2),
            },
          ],
        };
      }

      const result = await runAgentTaskRlmSession({
        provider,
        model,
        config: {
          enabled: true,
          model: args.rlmModel,
          maxTurns: args.rlmMaxTurns ?? 6,
          maxTokensPerTurn: args.rlmMaxTokensPerTurn ?? 2048,
          temperature: args.rlmTemperature ?? 0.2,
          maxStdoutChars: args.rlmMaxStdoutChars ?? 8192,
          codeTimeoutMs: args.rlmCodeTimeoutMs ?? 10000,
          memoryLimitMb: args.rlmMemoryLimitMb ?? 64,
        },
        phase: args.phase,
        taskPrompt: args.taskPrompt,
        rubric: args.rubric,
        currentOutput: args.currentOutput,
        referenceContext: args.referenceContext,
        requiredConcepts: args.requiredConcepts,
      });

      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    },
  );

  // -- queue_task --
  server.tool(
    "queue_task",
    "Add a task to the background runner queue",
    {
      specName: z.string().describe("Task spec name / identifier"),
      taskPrompt: z.string().optional(),
      rubric: z.string().optional(),
      initialOutput: z.string().optional(),
      maxRounds: z.number().int().optional(),
      qualityThreshold: z.number().optional(),
      priority: z.number().int().default(0),
      rlmEnabled: z.boolean().optional(),
      rlmModel: z.string().optional(),
      rlmMaxTurns: z.number().int().positive().optional(),
      rlmMaxTokensPerTurn: z.number().int().positive().optional(),
      rlmTemperature: z.number().min(0).max(2).optional(),
      rlmMaxStdoutChars: z.number().int().positive().optional(),
      rlmCodeTimeoutMs: z.number().int().positive().optional(),
      rlmMemoryLimitMb: z.number().int().positive().optional(),
    },
    async (args) => {
      const taskId = enqueueTask(store, args.specName, {
        taskPrompt: args.taskPrompt,
        rubric: args.rubric,
        initialOutput: args.initialOutput,
        maxRounds: args.maxRounds,
        qualityThreshold: args.qualityThreshold,
        priority: args.priority,
        rlmEnabled: args.rlmEnabled,
        rlmModel: args.rlmModel,
        rlmMaxTurns: args.rlmMaxTurns,
        rlmMaxTokensPerTurn: args.rlmMaxTokensPerTurn,
        rlmTemperature: args.rlmTemperature,
        rlmMaxStdoutChars: args.rlmMaxStdoutChars,
        rlmCodeTimeoutMs: args.rlmCodeTimeoutMs,
        rlmMemoryLimitMb: args.rlmMemoryLimitMb,
      });
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify({ taskId, specName: args.specName, status: "queued" }),
          },
        ],
      };
    },
  );

  // -- get_queue_status --
  server.tool(
    "get_queue_status",
    "Get task queue status summary",
    {},
    async () => {
      const pending = store.pendingTaskCount();
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify({ pendingCount: pending }),
          },
        ],
      };
    },
  );

  // -- get_task_result --
  server.tool(
    "get_task_result",
    "Get the result of a queued task by ID",
    {
      taskId: z.string().describe("Task ID to look up"),
    },
    async (args) => {
      const task = store.getTask(args.taskId);
      if (!task) {
        return {
          content: [{ type: "text" as const, text: JSON.stringify({ error: "Task not found" }) }],
        };
      }
      const result: Record<string, unknown> = {
        id: task.id,
        specName: task.spec_name,
        status: task.status,
        priority: task.priority,
        createdAt: task.created_at,
      };
      if (task.status === "completed") {
        result.bestScore = task.best_score;
        result.totalRounds = task.total_rounds;
        result.metThreshold = !!task.met_threshold;
        result.bestOutput = task.best_output;
        result.completedAt = task.completed_at;
      } else if (task.status === "failed") {
        result.error = task.error;
      }
      return {
        content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }],
      };
    },
  );

  // -- list_scenarios (AC-312) --
  server.tool(
    "list_scenarios",
    "List available scenarios with metadata",
    {},
    async () => {
      const { SCENARIO_REGISTRY } = await import("../scenarios/registry.js");
      const scenarios = Object.keys(SCENARIO_REGISTRY).sort().map((name) => {
        const instance = new SCENARIO_REGISTRY[name]();
        return {
          name,
          rules: instance.describeRules(),
          strategyInterface: instance.describeStrategyInterface(),
        };
      });
      return {
        content: [{ type: "text" as const, text: JSON.stringify({ scenarios }, null, 2) }],
      };
    },
  );

  // -- get_scenario (AC-312) --
  server.tool(
    "get_scenario",
    "Get detailed information about a scenario",
    {
      name: z.string().describe("Scenario name"),
    },
    async (args) => {
      const { SCENARIO_REGISTRY } = await import("../scenarios/registry.js");
      const ScenarioClass = SCENARIO_REGISTRY[args.name];
      if (!ScenarioClass) {
        return {
          content: [{ type: "text" as const, text: JSON.stringify({ error: `Unknown scenario: ${args.name}` }) }],
        };
      }
      const instance = new ScenarioClass();
      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            name: args.name,
            rules: instance.describeRules(),
            strategyInterface: instance.describeStrategyInterface(),
            evaluationCriteria: instance.describeEvaluationCriteria(),
            scoringDimensions: instance.scoringDimensions?.() ?? null,
          }, null, 2),
        }],
      };
    },
  );

  // -- list_runs (AC-312) --
  server.tool(
    "list_runs",
    "List recent runs with optional filters",
    {
      limit: z.number().int().default(50).describe("Max runs to return"),
      scenario: z.string().optional().describe("Filter by scenario name"),
    },
    async (args) => {
      const runs = store.listRuns(args.limit, args.scenario);
      return {
        content: [{ type: "text" as const, text: JSON.stringify({ runs }, null, 2) }],
      };
    },
  );

  // -- get_run_status (AC-312) --
  server.tool(
    "get_run_status",
    "Get run progress, scores, and generation details",
    {
      runId: z.string().describe("Run ID"),
    },
    async (args) => {
      const run = store.getRun(args.runId);
      if (!run) {
        return {
          content: [{ type: "text" as const, text: JSON.stringify({ error: "Run not found" }) }],
        };
      }
      const generations = store.getGenerations(args.runId);
      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({ ...run, generations }, null, 2),
        }],
      };
    },
  );

  // -- get_playbook (AC-312) --
  server.tool(
    "get_playbook",
    "Read the accumulated playbook for a scenario",
    {
      scenario: z.string().describe("Scenario name"),
    },
    async (args) => {
      const { ArtifactStore } = await import("../knowledge/artifact-store.js");
      const artifacts = new ArtifactStore({
        runsRoot,
        knowledgeRoot,
      });
      const content = artifacts.readPlaybook(args.scenario);
      return {
        content: [{ type: "text" as const, text: JSON.stringify({ scenario: args.scenario, content }, null, 2) }],
      };
    },
  );

  // -- run_scenario (AC-312) --
  server.tool(
    "run_scenario",
    "Kick off a scenario run with configuration options",
    {
      scenario: z.string().describe("Scenario name"),
      generations: z.number().int().default(1).describe("Number of generations"),
      runId: z.string().optional().describe("Custom run ID"),
      matchesPerGeneration: z.number().int().default(3).describe("Matches per generation"),
    },
    async (args) => {
      const { SCENARIO_REGISTRY } = await import("../scenarios/registry.js");
      const { GenerationRunner } = await import("../loop/generation-runner.js");

      const ScenarioClass = SCENARIO_REGISTRY[args.scenario];
      if (!ScenarioClass) {
        return {
          content: [{ type: "text" as const, text: JSON.stringify({ error: `Unknown scenario: ${args.scenario}` }) }],
        };
      }

      const runId = args.runId ?? `mcp-${Date.now()}`;
      const runner = new GenerationRunner({
        provider,
        scenario: new ScenarioClass(),
        store,
        runsRoot,
        knowledgeRoot,
        matchesPerGeneration: args.matchesPerGeneration,
        maxRetries: settings.maxRetries,
        minDelta: settings.backpressureMinDelta,
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
      });

      // Fire and forget — run in background
      runner.run(runId, args.generations).catch(() => {});

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({ runId, scenario: args.scenario, generations: args.generations, status: "started" }),
        }],
      };
    },
  );

  // -- get_generation_detail (AC-312) --
  server.tool(
    "get_generation_detail",
    "Get detailed results for a specific generation",
    {
      runId: z.string().describe("Run ID"),
      generation: z.number().int().describe("Generation index"),
    },
    async (args) => {
      const generations = store.getGenerations(args.runId);
      const gen = generations.find((g) => g.generation_index === args.generation);
      if (!gen) {
        return {
          content: [{ type: "text" as const, text: JSON.stringify({ error: "Generation not found" }) }],
        };
      }
      const matches = store.getMatchesForGeneration(args.runId, args.generation);
      const agentOutputs = store.getAgentOutputs(args.runId, args.generation);
      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            generation: gen,
            matches,
            agentOutputs: agentOutputs.map((o) => ({
              role: o.role,
              contentPreview: o.content.slice(0, 500),
            })),
          }, null, 2),
        }],
      };
    },
  );

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
