/**
 * MCP server for autocontext — expanded package control plane.
 * Covers evaluation, scenarios, runs, knowledge, feedback, and exports.
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";
import type { LLMProvider } from "../types/index.js";
import { DelegatedJudge, LLMJudge, SequentialDelegatedJudge } from "../judge/index.js";
import type { DelegatedResult } from "../judge/index.js";
import { ImprovementLoop } from "../execution/improvement-loop.js";
import { enqueueTask } from "../execution/task-runner.js";
import { SandboxManager } from "../execution/sandbox.js";
import { SQLiteStore } from "../storage/index.js";
import { SimpleAgentTask } from "../execution/task-runner.js";
import { loadSettings } from "../config/index.js";
import { SolveManager } from "../knowledge/solver.js";
import { runAgentTaskRlmSession } from "../rlm/index.js";
import { assertFamilyContract } from "../scenarios/family-interfaces.js";
import { registerMissionTools } from "./mission-tools.js";

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

function readReplayArtifact(
  runsRoot: string,
  runId: string,
  generation: number,
): Record<string, unknown> {
  const replayDir = join(
    runsRoot,
    runId,
    "generations",
    `gen_${generation}`,
    "replays",
  );
  if (!existsSync(replayDir)) {
    return { error: `no replay directory for run=${runId} gen=${generation}` };
  }
  const replayFiles = readdirSync(replayDir)
    .filter((name) => name.endsWith(".json"))
    .sort();
  if (replayFiles.length === 0) {
    return { error: `no replay files under ${replayDir}` };
  }
  return JSON.parse(readFileSync(join(replayDir, replayFiles[0]), "utf-8")) as Record<string, unknown>;
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
  const DelegatedResultArgSchema = z.object({
    score: z.number().min(0).max(1),
    reasoning: z.string(),
    dimensionScores: z.record(z.number().min(0).max(1)).optional(),
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
      delegatedResult: DelegatedResultArgSchema.optional().describe("Pre-computed evaluation from the calling agent"),
    },
    async (args) => {
      const judge = args.delegatedResult
        ? new DelegatedJudge(args.delegatedResult, args.rubric)
        : new LLMJudge({ provider, model, rubric: args.rubric });
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
      delegatedResults: z.array(DelegatedResultArgSchema).optional()
        .describe("Pre-computed per-round evaluations from the calling agent"),
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
      const delegatedJudge = args.delegatedResults?.length
        ? new SequentialDelegatedJudge(args.delegatedResults as DelegatedResult[], args.rubric)
        : undefined;
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
        delegatedJudge,
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
      delegatedResults: z.array(DelegatedResultArgSchema).optional(),
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
        delegatedResults: args.delegatedResults as DelegatedResult[] | undefined,
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
      assertFamilyContract(instance, "game", `scenario '${args.name}'`);
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

  // -- revise_scenario (AC-441) --
  server.tool(
    "revise_scenario",
    "Revise a scenario spec based on user feedback. Takes the current spec and feedback, returns an updated spec via LLM.",
    {
      currentSpec: z.record(z.unknown()).describe("The current scenario spec to revise"),
      feedback: z.string().describe("User feedback describing what to change"),
      family: z.string().default("agent_task").describe("Scenario family (agent_task, simulation, etc.)"),
    },
    async (args) => {
      const { reviseSpec } = await import("../scenarios/scenario-revision.js");
      const result = await reviseSpec({
        currentSpec: args.currentSpec,
        feedback: args.feedback,
        family: args.family,
        provider,
      });
      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            changesApplied: result.changesApplied,
            revised: result.revised,
            error: result.error ?? null,
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
      const scenario = new ScenarioClass();
      assertFamilyContract(scenario, "game", `scenario '${args.scenario}'`);
      const runner = new GenerationRunner({
        provider,
        scenario,
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

  // -- validate_strategy (AC-365) --
  server.tool(
    "validate_strategy",
    "Validate a strategy JSON against a scenario's constraints",
    { scenario: z.string(), strategy: z.string() },
    async (args) => {
      const { SCENARIO_REGISTRY } = await import("../scenarios/registry.js");
      const Cls = SCENARIO_REGISTRY[args.scenario];
      if (!Cls) return { content: [{ type: "text" as const, text: JSON.stringify({ error: `Unknown scenario: ${args.scenario}` }) }] };
      const inst = new Cls();
      let strat: Record<string, unknown>;
      try { strat = JSON.parse(args.strategy); } catch { return { content: [{ type: "text" as const, text: JSON.stringify({ valid: false, reason: "Invalid JSON" }) }] }; }
      const [valid, reason] = inst.validateActions(inst.initialState(42), "challenger", strat);
      return { content: [{ type: "text" as const, text: JSON.stringify({ valid, reason }) }] };
    },
  );

  // -- run_match (AC-365) --
  server.tool(
    "run_match",
    "Execute a single match for a scenario",
    { scenario: z.string(), strategy: z.string(), seed: z.number().int().default(42) },
    async (args) => {
      const { SCENARIO_REGISTRY } = await import("../scenarios/registry.js");
      const Cls = SCENARIO_REGISTRY[args.scenario];
      if (!Cls) return { content: [{ type: "text" as const, text: JSON.stringify({ error: `Unknown scenario: ${args.scenario}` }) }] };
      let strat: Record<string, unknown>;
      try { strat = JSON.parse(args.strategy); } catch { return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Invalid JSON" }) }] }; }
      const result = new Cls().executeMatch(strat, args.seed);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    },
  );

  // -- run_tournament (AC-365) --
  server.tool(
    "run_tournament",
    "Run N matches with Elo scoring",
    { scenario: z.string(), strategy: z.string(), matches: z.number().int().default(3), seedBase: z.number().int().default(1000) },
    async (args) => {
      const { SCENARIO_REGISTRY } = await import("../scenarios/registry.js");
      const { TournamentRunner } = await import("../execution/tournament.js");
      const Cls = SCENARIO_REGISTRY[args.scenario];
      if (!Cls) return { content: [{ type: "text" as const, text: JSON.stringify({ error: `Unknown scenario: ${args.scenario}` }) }] };
      let strat: Record<string, unknown>;
      try { strat = JSON.parse(args.strategy); } catch { return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Invalid JSON" }) }] }; }
      const r = new TournamentRunner(new Cls(), { matchCount: args.matches, seedBase: args.seedBase });
      const result = r.run(strat);
      return { content: [{ type: "text" as const, text: JSON.stringify({ meanScore: result.meanScore, bestScore: result.bestScore, elo: result.elo, wins: result.wins, losses: result.losses }, null, 2) }] };
    },
  );

  // -- read_trajectory (AC-365) --
  server.tool(
    "read_trajectory",
    "Read the score trajectory for a run as markdown",
    { runId: z.string() },
    async (args) => {
      const { ScoreTrajectoryBuilder } = await import("../knowledge/trajectory.js");
      const traj = store.getScoreTrajectory(args.runId);
      return { content: [{ type: "text" as const, text: new ScoreTrajectoryBuilder(traj).build() || "No trajectory data." }] };
    },
  );

  // -- read_hints (AC-365) --
  server.tool(
    "read_hints",
    "Read competitor hints for a scenario",
    { scenario: z.string() },
    async (args) => {
      const { ArtifactStore } = await import("../knowledge/artifact-store.js");
      const { extractDelimitedSection } = await import("../agents/roles.js");
      const artifacts = new ArtifactStore({ runsRoot, knowledgeRoot });
      const playbook = artifacts.readPlaybook(args.scenario);
      const hints = extractDelimitedSection(playbook, "<!-- COMPETITOR_HINTS_START -->", "<!-- COMPETITOR_HINTS_END -->") ?? "";
      return { content: [{ type: "text" as const, text: hints || "No hints available." }] };
    },
  );

  // -- read_analysis (AC-365) --
  server.tool(
    "read_analysis",
    "Read the analyst output for a specific generation",
    { runId: z.string(), generation: z.number().int() },
    async (args) => {
      const outputs = store.getAgentOutputs(args.runId, args.generation);
      const analyst = outputs.find((o) => o.role === "analyst");
      return { content: [{ type: "text" as const, text: analyst?.content ?? "No analysis found." }] };
    },
  );

  // -- read_tools (AC-365) --
  server.tool(
    "read_tools",
    "Read architect-generated tools for a scenario",
    { scenario: z.string() },
    async (args) => {
      const { existsSync, readdirSync, readFileSync } = await import("node:fs");
      const { join } = await import("node:path");
      const toolsDir = join(knowledgeRoot, args.scenario, "tools");
      if (!existsSync(toolsDir)) return { content: [{ type: "text" as const, text: "No tools directory." }] };
      const files = readdirSync(toolsDir).filter((f: string) => f.endsWith(".py") || f.endsWith(".ts"));
      const tools = files.map((f: string) => ({ name: f, code: readFileSync(join(toolsDir, f), "utf-8") }));
      return { content: [{ type: "text" as const, text: JSON.stringify(tools, null, 2) }] };
    },
  );

  // -- read_skills (AC-365) --
  server.tool(
    "read_skills",
    "Read skill notes for a scenario",
    { scenario: z.string() },
    async (args) => {
      const { existsSync, readFileSync } = await import("node:fs");
      const { join } = await import("node:path");
      const skillPath = join(knowledgeRoot, args.scenario, "SKILL.md");
      if (!existsSync(skillPath)) return { content: [{ type: "text" as const, text: "No skill notes found." }] };
      return { content: [{ type: "text" as const, text: readFileSync(skillPath, "utf-8") }] };
    },
  );

  // -- export_skill (AC-365) --
  server.tool(
    "export_skill",
    "Export a portable skill package with markdown for agent install",
    { scenario: z.string() },
    async (args) => {
      const { ArtifactStore } = await import("../knowledge/artifact-store.js");
      const { exportStrategyPackage } = await import("../knowledge/package.js");
      const artifacts = new ArtifactStore({ runsRoot, knowledgeRoot });
      const pkg = exportStrategyPackage({
        scenarioName: args.scenario,
        artifacts,
        store,
      });
      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            ...pkg,
            suggested_filename: `${args.scenario.replace(/_/g, "-")}-knowledge.md`,
          }, null, 2),
        }],
      };
    },
  );

  // -- list_solved (AC-365) --
  server.tool(
    "list_solved",
    "List scenarios with exported knowledge or completed runs",
    {},
    async () => {
      const { existsSync, readdirSync } = await import("node:fs");
      const { join } = await import("node:path");
      const solved: Array<{ scenario: string; hasPlaybook: boolean }> = [];
      if (existsSync(knowledgeRoot)) {
        for (const name of readdirSync(knowledgeRoot)) {
          if (name.startsWith("_")) continue;
          const hasPlaybook = existsSync(join(knowledgeRoot, name, "playbook.md"));
          if (hasPlaybook) solved.push({ scenario: name, hasPlaybook });
        }
      }
      return { content: [{ type: "text" as const, text: JSON.stringify(solved, null, 2) }] };
    },
  );

  // -- search_strategies (AC-365) --
  server.tool(
    "search_strategies",
    "Search past strategies by keyword",
    { query: z.string(), limit: z.number().int().default(5) },
    async (args) => {
      const runs = store.listRuns(100);
      const results: Array<{ runId: string; scenario: string; generation: number; score: number; strategy: string }> = [];
      const queryLower = args.query.toLowerCase();
      for (const run of runs) {
        const gens = store.getGenerations(run.run_id);
        for (const gen of gens) {
          const outputs = store.getAgentOutputs(run.run_id, gen.generation_index);
          const comp = outputs.find((o) => o.role === "competitor");
          if (comp && comp.content.toLowerCase().includes(queryLower)) {
            results.push({ runId: run.run_id, scenario: run.scenario, generation: gen.generation_index, score: gen.best_score, strategy: comp.content.slice(0, 200) });
            if (results.length >= args.limit) break;
          }
        }
        if (results.length >= args.limit) break;
      }
      return { content: [{ type: "text" as const, text: JSON.stringify(results, null, 2) }] };
    },
  );

  // -- record_feedback (AC-365) --
  server.tool(
    "record_feedback",
    "Record human feedback for a scenario evaluation",
    { scenario: z.string(), agentOutput: z.string(), score: z.number().min(0).max(1).optional(), notes: z.string().default(""), generationId: z.string().optional() },
    async (args) => {
      const id = store.insertHumanFeedback(args.scenario, args.agentOutput, args.score, args.notes, args.generationId);
      return { content: [{ type: "text" as const, text: JSON.stringify({ feedbackId: id, scenario: args.scenario }) }] };
    },
  );

  // -- get_feedback (AC-365) --
  server.tool(
    "get_feedback",
    "Retrieve human feedback for a scenario",
    { scenario: z.string(), limit: z.number().int().default(10) },
    async (args) => {
      const feedback = store.getHumanFeedback(args.scenario, args.limit);
      return { content: [{ type: "text" as const, text: JSON.stringify(feedback, null, 2) }] };
    },
  );

  // -- run_replay (AC-365) --
  server.tool(
    "run_replay",
    "Read replay JSON for a specific generation",
    { runId: z.string(), generation: z.number().int() },
    async (args) => {
      const payload = readReplayArtifact(runsRoot, args.runId, args.generation);
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload, null, 2) }],
      };
    },
  );

  // -- solve_scenario (AC-370) --
  server.tool(
    "solve_scenario",
    "Submit a problem for on-demand solving. Returns a job_id for polling.",
    { description: z.string(), generations: z.number().int().default(5) },
    async (args) => {
      const jobId = solveManager.submit(args.description, args.generations);
      return { content: [{ type: "text" as const, text: JSON.stringify({ jobId, status: "pending" }) }] };
    },
  );

  // -- solve_status (AC-370) --
  server.tool(
    "solve_status",
    "Check status of a solve-on-demand job",
    { jobId: z.string() },
    async (args) => {
      return { content: [{ type: "text" as const, text: JSON.stringify(solveManager.getStatus(args.jobId), null, 2) }] };
    },
  );

  // -- solve_result (AC-370) --
  server.tool(
    "solve_result",
    "Get the exported skill package result of a completed solve-on-demand job",
    { jobId: z.string() },
    async (args) => {
      const result = solveManager.getResult(args.jobId);
      if (!result) {
        return {
          content: [{ type: "text" as const, text: JSON.stringify({ error: "Job not completed or not found", jobId: args.jobId }) }],
        };
      }
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    },
  );

  // -- sandbox_create (AC-370) --
  server.tool(
    "sandbox_create",
    "Create an isolated sandbox for scenario execution",
    { scenario: z.string(), userId: z.string().default("anonymous") },
    async (args) => {
      const sb = sandboxManager.create(args.scenario, args.userId);
      return { content: [{ type: "text" as const, text: JSON.stringify(sb, null, 2) }] };
    },
  );

  // -- sandbox_run (AC-370) --
  server.tool(
    "sandbox_run",
    "Run generation(s) in a sandbox",
    { sandboxId: z.string(), generations: z.number().int().default(1) },
    async (args) => {
      const result = await sandboxManager.run(args.sandboxId, args.generations);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    },
  );

  // -- sandbox_status (AC-370) --
  server.tool(
    "sandbox_status",
    "Get sandbox status",
    { sandboxId: z.string() },
    async (args) => {
      const sandbox = sandboxManager.getStatus(args.sandboxId);
      if (!sandbox) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: `Sandbox '${args.sandboxId}' not found` }) }] };
      }
      return { content: [{ type: "text" as const, text: JSON.stringify(sandbox, null, 2) }] };
    },
  );

  // -- sandbox_playbook (AC-370) --
  server.tool(
    "sandbox_playbook",
    "Read the current playbook for a sandbox",
    { sandboxId: z.string() },
    async (args) => {
      return { content: [{ type: "text" as const, text: sandboxManager.readPlaybook(args.sandboxId) }] };
    },
  );

  // -- sandbox_list (AC-370) --
  server.tool(
    "sandbox_list",
    "List active sandboxes",
    {},
    async () => {
      return { content: [{ type: "text" as const, text: JSON.stringify(sandboxManager.list(), null, 2) }] };
    },
  );

  // -- sandbox_destroy (AC-370) --
  server.tool(
    "sandbox_destroy",
    "Destroy a sandbox and clean up its data",
    { sandboxId: z.string() },
    async (args) => {
      return { content: [{ type: "text" as const, text: JSON.stringify({ destroyed: sandboxManager.destroy(args.sandboxId), sandboxId: args.sandboxId }) }] };
    },
  );

  // -- create_agent_task (AC-370) --
  server.tool(
    "create_agent_task",
    "Create a named agent task spec for evaluation",
    { name: z.string(), taskPrompt: z.string(), rubric: z.string(), referenceContext: z.string().optional() },
    async (args) => {
      const { AgentTaskStore } = await import("../scenarios/agent-task-store.js");
      const taskStore = new AgentTaskStore(join(knowledgeRoot, "_agent_tasks"));
      taskStore.create({ name: args.name, taskPrompt: args.taskPrompt, rubric: args.rubric, referenceContext: args.referenceContext });
      return { content: [{ type: "text" as const, text: JSON.stringify({ name: args.name, created: true }) }] };
    },
  );

  // -- list_agent_tasks (AC-370) --
  server.tool(
    "list_agent_tasks",
    "List created agent task specs",
    {},
    async () => {
      const { AgentTaskStore } = await import("../scenarios/agent-task-store.js");
      const taskStore = new AgentTaskStore(join(knowledgeRoot, "_agent_tasks"));
      return { content: [{ type: "text" as const, text: JSON.stringify(taskStore.list(), null, 2) }] };
    },
  );

  // -- get_agent_task (AC-370) --
  server.tool(
    "get_agent_task",
    "Get a specific agent task spec by name",
    { name: z.string() },
    async (args) => {
      const { AgentTaskStore } = await import("../scenarios/agent-task-store.js");
      const taskStore = new AgentTaskStore(join(knowledgeRoot, "_agent_tasks"));
      const task = taskStore.get(args.name);
      if (!task) return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Task not found" }) }] };
      return { content: [{ type: "text" as const, text: JSON.stringify(task, null, 2) }] };
    },
  );

  // -- generate_output (AC-370) --
  server.tool(
    "generate_output",
    "Generate an initial agent output for a task prompt",
    { taskPrompt: z.string(), systemPrompt: z.string().default("") },
    async (args) => {
      const result = await provider.complete({ systemPrompt: args.systemPrompt, userPrompt: args.taskPrompt });
      return { content: [{ type: "text" as const, text: JSON.stringify({ output: result.text, model: result.model }) }] };
    },
  );

  // -- export_package (AC-370) --
  server.tool(
    "export_package",
    "Export a versioned strategy package for a scenario",
    { scenario: z.string() },
    async (args) => {
      const { ArtifactStore } = await import("../knowledge/artifact-store.js");
      const { exportStrategyPackage } = await import("../knowledge/package.js");
      const artifacts = new ArtifactStore({ runsRoot, knowledgeRoot });
      const pkg = exportStrategyPackage({ scenarioName: args.scenario, artifacts, store });
      return { content: [{ type: "text" as const, text: JSON.stringify(pkg, null, 2) }] };
    },
  );

  // -- import_package (AC-370) --
  server.tool(
    "import_package",
    "Import a strategy package into scenario knowledge",
    { packageData: z.string(), conflictPolicy: z.string().default("merge") },
    async (args) => {
      const { ArtifactStore } = await import("../knowledge/artifact-store.js");
      const { importStrategyPackage } = await import("../knowledge/package.js");
      const { loadSettings } = await import("../config/index.js");
      const settings = loadSettings();
      const artifacts = new ArtifactStore({ runsRoot, knowledgeRoot });
      const pkg = JSON.parse(args.packageData) as Record<string, unknown>;
      const result = importStrategyPackage({ rawPackage: pkg, artifacts, skillsRoot: settings.skillsRoot, conflictPolicy: args.conflictPolicy as "overwrite" | "merge" | "skip" });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    },
  );

  // -- capabilities (AC-370) --
  server.tool(
    "capabilities",
    "Return capability metadata for this autocontext instance",
    {},
    async () => {
      const { getCapabilities } = await import("./capabilities.js");
      return { content: [{ type: "text" as const, text: JSON.stringify(getCapabilities(), null, 2) }] };
    },
  );

  registerMissionTools(server, {
    dbPath: opts.dbPath ?? settings.dbPath,
    runsRoot,
  });

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
