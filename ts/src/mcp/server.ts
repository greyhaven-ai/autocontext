/**
 * MCP server for AutoContext — agent task evaluation tools.
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
import { runAgentTaskRlmSession } from "../rlm/index.js";

export interface MtsServerOpts {
  store: SQLiteStore;
  provider: LLMProvider;
  model?: string;
  /** Directory for agent task spec JSON files */
  tasksDir?: string;
}

export function createMcpServer(opts: MtsServerOpts): McpServer {
  const { store, provider, model = "claude-sonnet-4-20250514" } = opts;
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
