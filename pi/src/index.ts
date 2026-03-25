/**
 * Official autocontext Pi extension (AC-427).
 *
 * Registers autocontext tools, commands, and event handlers
 * inside the Pi coding agent environment.
 *
 * Tool execute() handlers use dynamic import("autoctx") at invocation time
 * so the extension loads instantly without requiring autoctx at registration.
 * Pi loads extensions via jiti, which handles TypeScript natively.
 */

import { Type } from "@sinclair/typebox";

/** Minimal Pi ExtensionAPI shape used by this extension. */
interface ExtensionAPI {
  registerTool(def: {
    name: string;
    label: string;
    description: string;
    promptSnippet?: string;
    promptGuidelines?: string[];
    parameters: unknown;
    execute: (
      toolCallId: string,
      params: Record<string, unknown>,
      signal: unknown,
      onUpdate?: (update: { content: Array<{ type: string; text: string }> }) => void,
      ctx?: unknown,
    ) => Promise<{ content: Array<{ type: string; text: string }>; details?: Record<string, unknown> }>;
  }): void;
  registerCommand(name: string, opts: { handler: (...args: unknown[]) => Promise<void> }): void;
  on(event: string, handler: (...args: unknown[]) => void): void;
}

type ToolResult = { content: Array<{ type: string; text: string }>; details?: Record<string, unknown> };

function ok(text: string, details?: Record<string, unknown>): ToolResult {
  return { content: [{ type: "text", text }], ...(details ? { details } : {}) };
}

function getStringParam(params: Record<string, unknown>, key: string): string | undefined {
  const value = params[key];
  return typeof value === "string" ? value : undefined;
}

function getNumberParam(params: Record<string, unknown>, key: string): number | undefined {
  const value = params[key];
  return typeof value === "number" ? value : undefined;
}

export default function autocontextExtension(pi: ExtensionAPI): void {
  // -----------------------------------------------------------------------
  // Tools
  // -----------------------------------------------------------------------

  pi.registerTool({
    name: "autocontext_judge",
    label: "AutoContext Judge",
    description: "Evaluate agent output against a rubric using LLM-based judging",
    promptSnippet: "Judge output quality using a rubric",
    promptGuidelines: [
      "Use when evaluating task quality against defined criteria.",
      "Requires an LLM provider to be configured.",
    ],
    parameters: Type.Object({
      task_prompt: Type.String({ description: "The task that was given to the agent" }),
      agent_output: Type.String({ description: "The agent's output to evaluate" }),
      rubric: Type.String({ description: "Evaluation criteria for judging" }),
      model: Type.Optional(Type.String({ description: "Model to use for judging" })),
    }),
    async execute(_toolCallId, params, _signal, onUpdate) {
      onUpdate?.({ content: [{ type: "text", text: "Evaluating output against rubric..." }] });
      try {
        const ac = await loadAutoctx();
        const provider = resolveProvider(ac);
        const judge = new ac.LLMJudge({
          provider,
          model: (params.model as string) || provider.defaultModel(),
          rubric: params.rubric as string,
        });
        const result = await judge.evaluate({
          taskPrompt: params.task_prompt as string,
          agentOutput: params.agent_output as string,
        });
        return ok(
          `Score: ${result.score.toFixed(2)}\nReasoning: ${result.reasoning}\nDimensions: ${JSON.stringify(result.dimensionScores, null, 2)}`,
          { score: result.score, dimensions: result.dimensionScores },
        );
      } catch (err) {
        return ok(`Judge error: ${(err as Error).message}`);
      }
    },
  });

  pi.registerTool({
    name: "autocontext_improve",
    label: "AutoContext Improve",
    description: "Run multi-round improvement loop on agent output with judge feedback",
    promptSnippet: "Iteratively improve output via judge-guided revision",
    parameters: Type.Object({
      task_prompt: Type.String({ description: "The task prompt" }),
      initial_output: Type.String({ description: "Initial agent output to improve" }),
      rubric: Type.String({ description: "Evaluation rubric" }),
      max_rounds: Type.Optional(Type.Number({ description: "Maximum improvement rounds (default 3)" })),
      quality_threshold: Type.Optional(Type.Number({ description: "Target quality score (default 0.9)" })),
    }),
    async execute(_toolCallId, params, _signal, onUpdate) {
      onUpdate?.({ content: [{ type: "text", text: "Starting improvement loop..." }] });
      try {
        const ac = await loadAutoctx();
        const provider = resolveProvider(ac);
        const task = new ac.SimpleAgentTask(
          params.task_prompt as string,
          params.rubric as string,
          provider,
          provider.defaultModel(),
        );
        const loop = new ac.ImprovementLoop({
          task,
          maxRounds: getNumberParam(params, "max_rounds") ?? 3,
          qualityThreshold: getNumberParam(params, "quality_threshold") ?? 0.9,
        });
        const result = await loop.run({
          initialOutput: params.initial_output as string,
          state: {},
        });
        return ok(
          `Improvement complete.\nFinal score: ${result.bestScore.toFixed(2)}\nRounds: ${result.rounds.length}\nOutput:\n${result.bestOutput}`,
          { bestScore: result.bestScore, rounds: result.rounds.length },
        );
      } catch (err) {
        return ok(`Improve error: ${(err as Error).message}`);
      }
    },
  });

  pi.registerTool({
    name: "autocontext_status",
    label: "AutoContext Status",
    description: "Check status of autocontext runs and tasks",
    promptSnippet: "Get the current status of autocontext runs",
    parameters: Type.Object({
      run_id: Type.Optional(Type.String({ description: "Specific run ID to query" })),
    }),
    async execute(_toolCallId, params) {
      try {
        const ac = await loadAutoctx();
        const store = resolveStore(ac);
        if (!store) return ok("No autocontext database found. Run `autoctx init` first.");
        const runs = store.listRuns();
        if (params.run_id) {
          const run = runs.find((r: { id: string }) => r.id === params.run_id);
          if (!run) return ok(`Run ${params.run_id} not found.`);
          return ok(JSON.stringify(run, null, 2), run as Record<string, unknown>);
        }
        return ok(
          `${runs.length} run(s) found.\n${runs.map((r: { id: string; status: string }) => `- ${r.id}: ${r.status}`).join("\n")}`,
          { count: runs.length },
        );
      } catch (err) {
        return ok(`Status error: ${(err as Error).message}`);
      }
    },
  });

  pi.registerTool({
    name: "autocontext_scenarios",
    label: "AutoContext Scenarios",
    description: "List available autocontext scenarios and their families",
    promptSnippet: "Discover available evaluation scenarios",
    parameters: Type.Object({
      family: Type.Optional(Type.String({ description: "Filter by scenario family" })),
    }),
    async execute(_toolCallId, params) {
      try {
        const ac = await loadAutoctx();
        const entries = Object.entries(ac.SCENARIO_REGISTRY);
        const filtered = params.family
          ? entries.filter(([, v]) => (v as { family?: string }).family === params.family)
          : entries;
        const lines = filtered.map(([name]) => `- ${name}`);
        return ok(
          `${filtered.length} scenario(s):\n${lines.join("\n")}`,
          { count: filtered.length, scenarios: filtered.map(([name]) => name) },
        );
      } catch (err) {
        return ok(`Scenarios error: ${(err as Error).message}`);
      }
    },
  });

  pi.registerTool({
    name: "autocontext_queue",
    label: "AutoContext Queue",
    description: "Enqueue a task for background evaluation by the task runner",
    promptSnippet: "Queue a task for asynchronous judge evaluation",
    parameters: Type.Object({
      spec_name: Type.String({ description: "Name of the spec/scenario to queue" }),
      task_prompt: Type.Optional(Type.String({ description: "Override task prompt" })),
      rubric: Type.Optional(Type.String({ description: "Override rubric" })),
      priority: Type.Optional(Type.Number({ description: "Task priority (higher = sooner)" })),
    }),
    async execute(_toolCallId, params, _signal, onUpdate) {
      onUpdate?.({ content: [{ type: "text", text: `Queueing task: ${params.spec_name}...` }] });
      try {
        const ac = await loadAutoctx();
        const store = resolveStore(ac);
        if (!store) return ok("No autocontext database found. Run `autoctx init` first.");
        ac.enqueueTask(store, params.spec_name as string, {
          taskPrompt: getStringParam(params, "task_prompt"),
          rubric: getStringParam(params, "rubric"),
          priority: getNumberParam(params, "priority"),
        });
        return ok(`Task '${params.spec_name}' queued successfully.`);
      } catch (err) {
        return ok(`Queue error: ${(err as Error).message}`);
      }
    },
  });

  // -----------------------------------------------------------------------
  // Slash command
  // -----------------------------------------------------------------------

  pi.registerCommand("autocontext", {
    handler: async () => {
      // The /autocontext command triggers the autocontext skill which
      // provides full instructions for using autocontext tools.
    },
  });

  // -----------------------------------------------------------------------
  // Lifecycle events
  // -----------------------------------------------------------------------

  pi.on("session_start", () => {
    // Future: auto-discover .autoctx.json and load project config
  });
}

// ---------------------------------------------------------------------------
// Internal helpers — lazy-load autoctx to keep extension registration instant
// ---------------------------------------------------------------------------

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AutoctxModule = any;

async function loadAutoctx(): Promise<AutoctxModule> {
  return await import("autoctx");
}

function resolveProvider(ac: AutoctxModule) {
  const settings = typeof ac.loadSettings === "function" ? ac.loadSettings() : {};
  const config = typeof ac.resolveProviderConfig === "function"
    ? ac.resolveProviderConfig()
    : {
        providerType: "anthropic",
        apiKey: process.env.ANTHROPIC_API_KEY ?? process.env.AUTOCONTEXT_API_KEY,
        model: process.env.AUTOCONTEXT_MODEL,
      };

  return ac.createProvider({
    ...config,
    piCommand: settings.piCommand,
    piTimeout: settings.piTimeout,
    piWorkspace: settings.piWorkspace,
    piModel: settings.piModel,
    piRpcEndpoint: settings.piRpcEndpoint,
    piRpcApiKey: settings.piRpcApiKey,
    piRpcSessionPersistence: settings.piRpcSessionPersistence,
  });
}

function resolveStore(ac: AutoctxModule) {
  try {
    const settings = typeof ac.loadSettings === "function" ? ac.loadSettings() : {};
    const dbPath = process.env.AUTOCONTEXT_DB_PATH ?? settings.dbPath ?? "runs/autocontext.sqlite3";
    return new ac.SQLiteStore(dbPath) as {
      listRuns: () => Array<{ id: string; status: string }>;
    };
  } catch {
    return null;
  }
}
