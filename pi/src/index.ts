/**
 * pi-autocontext — Official autocontext Pi extension.
 *
 * Registers autocontext tools, commands, and event handlers
 * inside the Pi coding agent environment.
 *
 * Tool execute() handlers use dynamic import("autoctx") at invocation time
 * so the extension loads instantly without requiring autoctx at registration.
 * Pi loads extensions via jiti, which handles TypeScript natively.
 */

import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { Text } from "@mariozechner/pi-tui";
import { Type } from "@sinclair/typebox";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function ok(text: string, details: Record<string, unknown> = {}) {
  return { content: [{ type: "text" as const, text }], details };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AutoctxModule = any;

async function loadAutoctx(): Promise<AutoctxModule> {
  return await import("autoctx");
}

function resolveProvider(ac: AutoctxModule) {
  const settings =
    typeof ac.loadSettings === "function" ? ac.loadSettings() : {};
  const config =
    typeof ac.resolveProviderConfig === "function"
      ? ac.resolveProviderConfig()
      : {
          providerType: "anthropic",
          apiKey:
            process.env.ANTHROPIC_API_KEY ?? process.env.AUTOCONTEXT_API_KEY,
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
    const settings =
      typeof ac.loadSettings === "function" ? ac.loadSettings() : {};
    const dbPath =
      process.env.AUTOCONTEXT_DB_PATH ??
      settings.dbPath ??
      "runs/autocontext.sqlite3";
    return new ac.SQLiteStore(dbPath) as {
      listRuns: () => Array<{ id: string; status: string }>;
    };
  } catch {
    return null;
  }
}

function renderScore(score: number): string {
  const pct = (score * 100).toFixed(0);
  if (score >= 0.8) return `✅ ${pct}%`;
  if (score >= 0.5) return `⚠️  ${pct}%`;
  return `❌ ${pct}%`;
}

// ---------------------------------------------------------------------------
// Extension entry point
// ---------------------------------------------------------------------------

export default function autocontextExtension(pi: ExtensionAPI): void {
  // -----------------------------------------------------------------------
  // Tool: autocontext_judge
  // -----------------------------------------------------------------------

  pi.registerTool({
    name: "autocontext_judge",
    label: "autocontext Judge",
    description:
      "Evaluate agent output against a rubric using LLM-based judging. Returns a 0–1 score with reasoning and dimension breakdowns.",
    promptSnippet: "Judge output quality against a rubric (0–1 score)",
    promptGuidelines: [
      "Use when evaluating task output quality against defined criteria.",
      "Requires an LLM provider to be configured.",
      "Returns a score (0–1), reasoning, and per-dimension breakdowns.",
    ],
    parameters: Type.Object({
      task_prompt: Type.String({
        description: "The task that was given to the agent",
      }),
      agent_output: Type.String({
        description: "The agent's output to evaluate",
      }),
      rubric: Type.String({
        description: "Evaluation criteria for judging",
      }),
      model: Type.Optional(
        Type.String({ description: "Model to use for judging" }),
      ),
    }),
    async execute(_toolCallId, params, _signal, onUpdate, _ctx) {
      onUpdate?.({ content: [{ type: "text", text: "Evaluating output against rubric…" }], details: {} });
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
        `Score: ${renderScore(result.score)}\nReasoning: ${result.reasoning}\nDimensions: ${JSON.stringify(result.dimensionScores, null, 2)}`,
        { score: result.score, dimensions: result.dimensionScores },
      );
    },
    renderCall(args, theme) {
      const label = theme.fg("toolTitle", theme.bold("autocontext judge "));
      const rubric = args.rubric
        ? theme.fg("dim", `rubric: "${(args.rubric as string).slice(0, 60)}"`)
        : "";
      return new Text(`${label}${rubric}`, 0, 0);
    },
    renderResult(result, _options, theme) {
      const details = result.details as { score?: number } | undefined;
      if (details?.score !== undefined) {
        const scoreText = renderScore(details.score);
        return new Text(theme.fg("accent", scoreText), 0, 0);
      }
      const text = result.content[0];
      return new Text(text?.type === "text" ? text.text : "", 0, 0);
    },
  });

  // -----------------------------------------------------------------------
  // Tool: autocontext_improve
  // -----------------------------------------------------------------------

  pi.registerTool({
    name: "autocontext_improve",
    label: "autocontext Improve",
    description:
      "Run multi-round improvement loop on agent output with judge feedback. Iterates until quality threshold or max rounds.",
    promptSnippet:
      "Iteratively improve output via judge-guided revision loops",
    promptGuidelines: [
      "Use when output quality needs iterative refinement with automated feedback.",
      "Set max_rounds (default 3) and quality_threshold (default 0.9) to control the loop.",
      "Each round re-evaluates and revises based on judge feedback.",
    ],
    parameters: Type.Object({
      task_prompt: Type.String({ description: "The task prompt" }),
      initial_output: Type.String({
        description: "Initial agent output to improve",
      }),
      rubric: Type.String({ description: "Evaluation rubric" }),
      max_rounds: Type.Optional(
        Type.Number({
          description: "Maximum improvement rounds (default 3)",
        }),
      ),
      quality_threshold: Type.Optional(
        Type.Number({
          description: "Target quality score 0–1 (default 0.9)",
        }),
      ),
    }),
    async execute(_toolCallId, params, _signal, onUpdate, _ctx) {
      onUpdate?.({ content: [{ type: "text", text: "Starting improvement loop…" }], details: {} });
      const ac = await loadAutoctx();
      const provider = resolveProvider(ac);
      const task = new ac.SimpleAgentTask(
        params.task_prompt as string,
        params.rubric as string,
        provider,
        provider.defaultModel(),
      );
      const maxRounds =
        typeof params.max_rounds === "number" ? params.max_rounds : 3;
      const threshold =
        typeof params.quality_threshold === "number"
          ? params.quality_threshold
          : 0.9;
      const loop = new ac.ImprovementLoop({
        task,
        maxRounds,
        qualityThreshold: threshold,
      });
      const result = await loop.run({
        initialOutput: params.initial_output as string,
        state: {},
      });
      return ok(
        `Improvement complete.\nFinal score: ${renderScore(result.bestScore)}\nRounds: ${result.rounds.length}/${maxRounds}\nOutput:\n${result.bestOutput}`,
        { bestScore: result.bestScore, rounds: result.rounds.length },
      );
    },
    renderCall(args, theme) {
      const label = theme.fg("toolTitle", theme.bold("autocontext improve "));
      const rounds = args.max_rounds ? theme.fg("muted", `max ${args.max_rounds} rounds`) : "";
      return new Text(`${label}${rounds}`, 0, 0);
    },
    renderResult(result, _options, theme) {
      const details = result.details as { bestScore?: number; rounds?: number } | undefined;
      if (details?.bestScore !== undefined) {
        return new Text(
          `${renderScore(details.bestScore)} after ${details.rounds ?? "?"} round(s)`,
          0,
          0,
        );
      }
      const text = result.content[0];
      return new Text(text?.type === "text" ? text.text : "", 0, 0);
    },
  });

  // -----------------------------------------------------------------------
  // Tool: autocontext_status
  // -----------------------------------------------------------------------

  pi.registerTool({
    name: "autocontext_status",
    label: "autocontext Status",
    description:
      "Check status of autocontext runs and tasks. Lists recent runs or shows details for a specific run.",
    promptSnippet: "Check status of autocontext runs and queued tasks",
    promptGuidelines: [
      "Use to check on evaluation progress or find recent run IDs.",
      "Pass run_id to get details for a specific run.",
      "Works without arguments to list all recent runs.",
    ],
    parameters: Type.Object({
      run_id: Type.Optional(
        Type.String({ description: "Specific run ID to query" }),
      ),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, _ctx) {
      const ac = await loadAutoctx();
      const store = resolveStore(ac);
      if (!store) {
        throw new Error(
          "No autocontext database found. Run `autoctx init` first.",
        );
      }
      const runs = store.listRuns();
      if (params.run_id) {
        const run = runs.find(
          (r: { id: string }) => r.id === params.run_id,
        );
        if (!run) throw new Error(`Run ${params.run_id} not found.`);
        return ok(
          JSON.stringify(run, null, 2),
          run as Record<string, unknown>,
        );
      }
      return ok(
        `${runs.length} run(s) found.\n${runs.map((r: { id: string; status: string }) => `- ${r.id}: ${r.status}`).join("\n")}`,
        { count: runs.length },
      );
    },
    renderCall(args, theme) {
      const label = theme.fg("toolTitle", theme.bold("autocontext status "));
      const id = args.run_id ? theme.fg("accent", args.run_id as string) : theme.fg("dim", "(all runs)");
      return new Text(`${label}${id}`, 0, 0);
    },
  });

  // -----------------------------------------------------------------------
  // Tool: autocontext_scenarios
  // -----------------------------------------------------------------------

  pi.registerTool({
    name: "autocontext_scenarios",
    label: "autocontext Scenarios",
    description:
      "List available autocontext evaluation scenarios and their families.",
    promptSnippet: "Discover available evaluation scenarios and families",
    promptGuidelines: [
      "Use to discover what scenarios are registered before running evaluations.",
      "Filter by family to narrow results (e.g. 'agent_task', 'simulation').",
    ],
    parameters: Type.Object({
      family: Type.Optional(
        Type.String({ description: "Filter by scenario family" }),
      ),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, _ctx) {
      const ac = await loadAutoctx();
      const entries = Object.entries(ac.SCENARIO_REGISTRY);
      const filtered = params.family
        ? entries.filter(
            ([, v]) =>
              (v as { family?: string }).family === params.family,
          )
        : entries;
      const lines = filtered.map(([name]) => `- ${name}`);
      return ok(
        `${filtered.length} scenario(s):\n${lines.join("\n")}`,
        {
          count: filtered.length,
          scenarios: filtered.map(([name]) => name),
        },
      );
    },
    renderCall(args, theme) {
      const label = theme.fg("toolTitle", theme.bold("autocontext scenarios "));
      const fam = args.family ? theme.fg("accent", args.family as string) : theme.fg("dim", "(all)");
      return new Text(`${label}${fam}`, 0, 0);
    },
  });

  // -----------------------------------------------------------------------
  // Tool: autocontext_queue
  // -----------------------------------------------------------------------

  pi.registerTool({
    name: "autocontext_queue",
    label: "autocontext Queue",
    description:
      "Enqueue a task for background evaluation by the task runner daemon.",
    promptSnippet: "Queue a task for asynchronous background evaluation",
    promptGuidelines: [
      "Use to queue evaluation tasks that run asynchronously in the background.",
      "Requires a spec name matching a registered scenario.",
      "Check results later with autocontext_status.",
    ],
    parameters: Type.Object({
      spec_name: Type.String({
        description: "Name of the spec/scenario to queue",
      }),
      task_prompt: Type.Optional(
        Type.String({ description: "Override task prompt" }),
      ),
      rubric: Type.Optional(
        Type.String({ description: "Override rubric" }),
      ),
      priority: Type.Optional(
        Type.Number({
          description: "Task priority (higher = sooner)",
        }),
      ),
    }),
    async execute(_toolCallId, params, _signal, onUpdate, _ctx) {
      onUpdate?.({ content: [{ type: "text", text: `Queueing task: ${params.spec_name}…` }], details: {} });
      const ac = await loadAutoctx();
      const store = resolveStore(ac);
      if (!store) {
        throw new Error(
          "No autocontext database found. Run `autoctx init` first.",
        );
      }
      ac.enqueueTask(store, params.spec_name as string, {
        taskPrompt:
          typeof params.task_prompt === "string"
            ? params.task_prompt
            : undefined,
        rubric:
          typeof params.rubric === "string" ? params.rubric : undefined,
        priority:
          typeof params.priority === "number" ? params.priority : undefined,
      });
      return ok(`Task '${params.spec_name}' queued successfully.`);
    },
    renderCall(args, theme) {
      const label = theme.fg("toolTitle", theme.bold("autocontext queue "));
      const spec = theme.fg("accent", args.spec_name as string);
      return new Text(`${label}${spec}`, 0, 0);
    },
  });

  // -----------------------------------------------------------------------
  // Slash commands
  // -----------------------------------------------------------------------

  pi.registerCommand("autocontext", {
    description: "Load the autocontext skill with full usage instructions",
    handler: async () => {
      // Triggers the autocontext skill which provides full instructions
    },
  });

  // -----------------------------------------------------------------------
  // Lifecycle events
  // -----------------------------------------------------------------------

  pi.on("session_start", async (_event, ctx) => {
    try {
      const { existsSync } = await import("node:fs");
      const { join } = await import("node:path");
      const configPath = join(ctx.cwd, ".autoctx.json");
      if (existsSync(configPath)) {
        ctx.ui.setStatus("autocontext", "autocontext project detected");
      }
    } catch {
      // Silently ignore — not critical
    }
  });
}
