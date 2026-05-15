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

import {
  DEFAULT_MAX_BYTES,
  DEFAULT_MAX_LINES,
  truncateTail,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import { Text } from "@earendil-works/pi-tui";
import { Type } from "typebox";
import {
  collectRuntimeSnapshot,
  isRecord,
  parseRuntimeSnapshotRequest,
  readString,
  renderRuntimeSnapshot,
  resolveSettings,
  resolveStore,
  runIdOf,
} from "./runtime-snapshot.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const TOOL_OUTPUT_LIMITS = {
  maxBytes: DEFAULT_MAX_BYTES,
  maxLines: DEFAULT_MAX_LINES,
} as const;

function ok(text: string, details: Record<string, unknown> = {}) {
  return { content: [{ type: "text" as const, text }], details };
}

function okTruncated(text: string, details: Record<string, unknown> = {}) {
  const truncated = truncateTail(text, TOOL_OUTPUT_LIMITS);
  return ok(
    truncated.content,
    truncated.truncated ? { ...details, outputTruncated: true } : details,
  );
}

function throwIfAborted(signal?: AbortSignal): void {
  if (!signal?.aborted) return;
  const reason: unknown = signal.reason;
  if (reason instanceof Error) throw reason;
  throw new Error(typeof reason === "string" ? reason : "autocontext tool execution aborted");
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AutoctxModule = any;

async function loadAutoctx(): Promise<AutoctxModule> {
  return await import("autoctx");
}

function resolveProvider(ac: AutoctxModule) {
  const settings = resolveSettings(ac);
  const config =
    typeof ac.resolveProviderConfig === "function"
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
      model: Type.Optional(Type.String({ description: "Model to use for judging" })),
    }),
    async execute(_toolCallId, params, signal, onUpdate, _ctx) {
      throwIfAborted(signal);
      onUpdate?.({
        content: [{ type: "text", text: "Evaluating output against rubric…" }],
        details: {},
      });
      const ac = await loadAutoctx();
      throwIfAborted(signal);
      const provider = resolveProvider(ac);
      const judge = new ac.LLMJudge({
        provider,
        model: (params.model as string) || provider.defaultModel(),
        rubric: params.rubric as string,
      });
      throwIfAborted(signal);
      const result = await judge.evaluate({
        taskPrompt: params.task_prompt as string,
        agentOutput: params.agent_output as string,
      });
      throwIfAborted(signal);
      return okTruncated(
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
    promptSnippet: "Iteratively improve output via judge-guided revision loops",
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
    async execute(_toolCallId, params, signal, onUpdate, _ctx) {
      throwIfAborted(signal);
      onUpdate?.({ content: [{ type: "text", text: "Starting improvement loop…" }], details: {} });
      const ac = await loadAutoctx();
      throwIfAborted(signal);
      const provider = resolveProvider(ac);
      const task = new ac.SimpleAgentTask(
        params.task_prompt as string,
        params.rubric as string,
        provider,
        provider.defaultModel(),
      );
      const maxRounds = typeof params.max_rounds === "number" ? params.max_rounds : 3;
      const threshold =
        typeof params.quality_threshold === "number" ? params.quality_threshold : 0.9;
      const loop = new ac.ImprovementLoop({
        task,
        maxRounds,
        qualityThreshold: threshold,
      });
      throwIfAborted(signal);
      const result = await loop.run({
        initialOutput: params.initial_output as string,
        state: {},
      });
      throwIfAborted(signal);
      return okTruncated(
        `Improvement complete.\nFinal score: ${renderScore(result.bestScore)}\nRounds: ${result.rounds.length}/${maxRounds}\nOutput:\n${result.bestOutput}`,
        { bestScore: result.bestScore, rounds: result.rounds.length },
      );
    },
    renderCall(args, theme) {
      const label = theme.fg("toolTitle", theme.bold("autocontext improve "));
      const rounds = args.max_rounds ? theme.fg("muted", `max ${args.max_rounds} rounds`) : "";
      return new Text(`${label}${rounds}`, 0, 0);
    },
    renderResult(result, _options, _theme) {
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
      run_id: Type.Optional(Type.String({ description: "Specific run ID to query" })),
    }),
    async execute(_toolCallId, params, signal, _onUpdate, _ctx) {
      throwIfAborted(signal);
      const ac = await loadAutoctx();
      throwIfAborted(signal);
      const store = resolveStore(ac);
      if (!store) {
        throw new Error("No autocontext database found. Run `autoctx init` first.");
      }
      try {
        if (typeof store.listRuns !== "function") {
          throw new Error("Installed autoctx SQLiteStore does not support listRuns.");
        }
        const runs = store.listRuns();
        throwIfAborted(signal);
        const requestedRunId = typeof params.run_id === "string" ? params.run_id : "";
        if (requestedRunId) {
          const run = runs.find((candidate) => runIdOf(candidate) === requestedRunId);
          if (!run) throw new Error(`Run ${requestedRunId} not found.`);
          return okTruncated(JSON.stringify(run, null, 2), run);
        }
        return okTruncated(
          `${runs.length} run(s) found.\n${runs.map((run) => `- ${runIdOf(run)}: ${readString(run, "status")}`).join("\n")}`,
          { count: runs.length },
        );
      } finally {
        store.close?.();
      }
    },

    renderCall(args, theme) {
      const label = theme.fg("toolTitle", theme.bold("autocontext status "));
      const id = args.run_id
        ? theme.fg("accent", args.run_id as string)
        : theme.fg("dim", "(all runs)");
      return new Text(`${label}${id}`, 0, 0);
    },
  });

  // -----------------------------------------------------------------------
  // Tool: autocontext_scenarios
  // -----------------------------------------------------------------------

  pi.registerTool({
    name: "autocontext_scenarios",
    label: "autocontext Scenarios",
    description: "List available autocontext evaluation scenarios and their families.",
    promptSnippet: "Discover available evaluation scenarios and families",
    promptGuidelines: [
      "Use to discover what scenarios are registered before running evaluations.",
      "Filter by family to narrow results (e.g. 'agent_task', 'simulation').",
    ],
    parameters: Type.Object({
      family: Type.Optional(Type.String({ description: "Filter by scenario family" })),
    }),
    async execute(_toolCallId, params, signal, _onUpdate, _ctx) {
      throwIfAborted(signal);
      const ac = await loadAutoctx();
      throwIfAborted(signal);
      const entries = Object.entries(ac.SCENARIO_REGISTRY);
      const filtered = params.family
        ? entries.filter(([, v]) => (v as { family?: string }).family === params.family)
        : entries;
      const lines = filtered.map(([name]) => `- ${name}`);
      return okTruncated(`${filtered.length} scenario(s):\n${lines.join("\n")}`, {
        count: filtered.length,
        scenarios: filtered.map(([name]) => name),
      });
    },
    renderCall(args, theme) {
      const label = theme.fg("toolTitle", theme.bold("autocontext scenarios "));
      const fam = args.family
        ? theme.fg("accent", args.family as string)
        : theme.fg("dim", "(all)");
      return new Text(`${label}${fam}`, 0, 0);
    },
  });

  // -----------------------------------------------------------------------
  // Tool: autocontext_queue
  // -----------------------------------------------------------------------

  pi.registerTool({
    name: "autocontext_queue",
    label: "autocontext Queue",
    description: "Enqueue a task for background evaluation by the task runner daemon.",
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
      task_prompt: Type.Optional(Type.String({ description: "Override task prompt" })),
      rubric: Type.Optional(Type.String({ description: "Override rubric" })),
      priority: Type.Optional(
        Type.Number({
          description: "Task priority (higher = sooner)",
        }),
      ),
    }),
    async execute(_toolCallId, params, signal, onUpdate, _ctx) {
      throwIfAborted(signal);
      onUpdate?.({
        content: [{ type: "text", text: `Queueing task: ${params.spec_name}…` }],
        details: {},
      });
      const ac = await loadAutoctx();
      throwIfAborted(signal);
      const store = resolveStore(ac);
      if (!store) {
        throw new Error("No autocontext database found. Run `autoctx init` first.");
      }
      try {
        ac.enqueueTask(store, params.spec_name as string, {
          taskPrompt: typeof params.task_prompt === "string" ? params.task_prompt : undefined,
          rubric: typeof params.rubric === "string" ? params.rubric : undefined,
          priority: typeof params.priority === "number" ? params.priority : undefined,
        });
        throwIfAborted(signal);
        return okTruncated(`Task '${params.spec_name}' queued successfully.`);
      } finally {
        store.close?.();
      }
    },
    renderCall(args, theme) {
      const label = theme.fg("toolTitle", theme.bold("autocontext queue "));
      const spec = theme.fg("accent", args.spec_name as string);
      return new Text(`${label}${spec}`, 0, 0);
    },
  });

  // -----------------------------------------------------------------------
  // Tool: autocontext_runtime_snapshot
  // -----------------------------------------------------------------------

  pi.registerTool({
    name: "autocontext_runtime_snapshot",
    label: "autocontext Runtime",
    description:
      "Inspect autocontext runtime state for Pi: run artifacts, package records, session branch lineage, and recent event-stream entries.",
    promptSnippet: "Inspect autocontext runs, packages, sessions, and event stream state",
    promptGuidelines: [
      "Use when you need run artifacts, package provenance, or session branch context before continuing work.",
      "Pass run_id for a specific run, session_id for branch lineage, or scenario to filter recent state.",
      "Set include_outputs only when output previews are needed; previews are truncated.",
    ],
    parameters: Type.Object({
      run_id: Type.Optional(Type.String({ description: "Specific autocontext run ID to inspect" })),
      session_id: Type.Optional(
        Type.String({ description: "Specific branchable session ID to inspect" }),
      ),
      scenario: Type.Optional(
        Type.String({ description: "Scenario name to filter recent runs and package records" }),
      ),
      limit: Type.Optional(
        Type.Number({ description: "Maximum rows/events to return, 1-50 (default 10)" }),
      ),
      generation_index: Type.Optional(
        Type.Number({
          description: "Generation index for output previews; defaults to latest generation",
        }),
      ),
      include_outputs: Type.Optional(
        Type.Boolean({
          description: "Include truncated agent output previews for the selected generation",
        }),
      ),
    }),
    async execute(_toolCallId, params, signal, onUpdate, _ctx) {
      throwIfAborted(signal);
      onUpdate?.({
        content: [{ type: "text", text: "Collecting autocontext runtime snapshot..." }],
        details: {},
      });
      const request = parseRuntimeSnapshotRequest(params);
      const ac = await loadAutoctx();
      throwIfAborted(signal);
      const settings = resolveSettings(ac);
      const store = resolveStore(ac);
      if (!store) {
        throw new Error("No autocontext database found. Run `autoctx init` first.");
      }
      try {
        throwIfAborted(signal);
        const snapshot = collectRuntimeSnapshot(ac, store, settings, request);
        throwIfAborted(signal);
        return okTruncated(renderRuntimeSnapshot(snapshot), snapshot);
      } finally {
        store.close?.();
      }
    },
    renderCall(args, theme) {
      const label = theme.fg("toolTitle", theme.bold("autocontext runtime "));
      const target = args.run_id
        ? theme.fg("accent", args.run_id as string)
        : args.session_id
          ? theme.fg("accent", args.session_id as string)
          : args.scenario
            ? theme.fg("accent", args.scenario as string)
            : theme.fg("dim", "(recent)");
      return new Text(`${label}${target}`, 0, 0);
    },
    renderResult(result, _options, theme) {
      const details = result.details as Record<string, unknown> | undefined;
      const run = details && isRecord(details.run) ? runIdOf(details.run) : "";
      const session =
        details && isRecord(details.session) ? readString(details.session, "sessionId") : "";
      const label = run || session || "snapshot";
      return new Text(theme.fg("accent", `runtime ${label}`), 0, 0);
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
