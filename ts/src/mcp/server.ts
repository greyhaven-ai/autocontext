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

export interface MtsServerOpts {
  store: SQLiteStore;
  provider: LLMProvider;
  model?: string;
  /** Directory for agent task spec JSON files */
  tasksDir?: string;
}

export function createMcpServer(opts: MtsServerOpts): McpServer {
  const { store, provider, model = "" } = opts;
  const server = new McpServer({
    name: "autocontext",
    version: "0.1.0",
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
      initialOutput: z.string().describe("Starting output to improve"),
      maxRounds: z.number().int().default(5).describe("Maximum improvement rounds"),
      qualityThreshold: z.number().default(0.9).describe("Score threshold to stop"),
      referenceContext: z.string().optional(),
      requiredConcepts: z.array(z.string()).optional(),
    },
    async (args) => {
      const task = new SimpleAgentTask(
        args.taskPrompt,
        args.rubric,
        provider,
        model,
      );
      const loop = new ImprovementLoop({
        task,
        maxRounds: args.maxRounds,
        qualityThreshold: args.qualityThreshold,
      });
      const result = await loop.run({
        initialOutput: args.initialOutput,
        state: {},
        referenceContext: args.referenceContext,
        requiredConcepts: args.requiredConcepts,
      });

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
              },
              null,
              2,
            ),
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
    },
    async (args) => {
      const taskId = enqueueTask(store, args.specName, {
        taskPrompt: args.taskPrompt,
        rubric: args.rubric,
        initialOutput: args.initialOutput,
        maxRounds: args.maxRounds,
        qualityThreshold: args.qualityThreshold,
        priority: args.priority,
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
