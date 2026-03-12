#!/usr/bin/env node
/**
 * AutoContext CLI — command-line interface for the evaluation harness.
 *
 * Commands:
 *   autoctx judge     — one-shot evaluation
 *   autoctx improve   — run improvement loop
 *   autoctx queue     — add task to background queue
 *   autoctx status    — check queue status
 *   autoctx serve     — start MCP server on stdio
 */

import { parseArgs } from "node:util";
import { resolve, join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

function getMigrationsDir(): string {
  const thisDir = dirname(fileURLToPath(import.meta.url));
  return join(thisDir, "..", "..", "migrations");
}

const HELP = `
autoctx — always-on agent evaluation harness

Commands:
  judge       One-shot evaluation of output against a rubric
  improve     Run multi-round improvement loop
  queue       Add a task to the background runner queue
  status      Show queue status
  serve       Start MCP server on stdio
  version     Show version

Run \`mts <command> --help\` for command-specific options.
`.trim();

async function main(): Promise<void> {
  const command = process.argv[2];

  if (!command || command === "--help" || command === "-h") {
    console.log(HELP);
    process.exit(0);
  }

  if (command === "version" || command === "--version") {
    const pkg = await import("../../package.json", { with: { type: "json" } });
    console.log(pkg.default.version);
    process.exit(0);
  }

  // All commands need a database
  const dbPath = process.env.AUTOCONTEXT_DB_PATH ?? resolve("autocontext.db");

  switch (command) {
    case "judge":
      await cmdJudge(dbPath);
      break;
    case "improve":
      await cmdImprove(dbPath);
      break;
    case "queue":
      await cmdQueue(dbPath);
      break;
    case "status":
      await cmdStatus(dbPath);
      break;
    case "serve":
      await cmdServe(dbPath);
      break;
    default:
      console.error(`Unknown command: ${command}\n`);
      console.log(HELP);
      process.exit(1);
  }
}

async function getProvider() {
  // Dynamic import to avoid loading heavy deps for --help
  const { ProviderError } = await import("../types/index.js");

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    console.error("ANTHROPIC_API_KEY environment variable required");
    process.exit(1);
  }

  const model = process.env.AUTOCONTEXT_MODEL ?? "claude-sonnet-4-20250514";

  // Simple fetch-based Anthropic provider
  const provider = {
    name: "anthropic-cli",
    defaultModel: () => model,
    complete: async (opts: {
      systemPrompt: string;
      userPrompt: string;
      model?: string;
      temperature?: number;
      maxTokens?: number;
    }) => {
      const res = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-api-key": apiKey,
          "anthropic-version": "2023-06-01",
        },
        body: JSON.stringify({
          model: opts.model ?? model,
          max_tokens: opts.maxTokens ?? 4096,
          temperature: opts.temperature ?? 0,
          system: opts.systemPrompt,
          messages: [{ role: "user", content: opts.userPrompt }],
        }),
      });

      if (!res.ok) {
        const body = await res.text();
        throw new ProviderError(`Anthropic API error ${res.status}: ${body.slice(0, 200)}`);
      }

      const data = (await res.json()) as {
        content: Array<{ type: string; text: string }>;
        model: string;
        usage: { input_tokens: number; output_tokens: number };
      };

      const text = data.content
        .filter((c) => c.type === "text")
        .map((c) => c.text)
        .join("");

      return {
        text,
        model: data.model,
        usage: {
          input: data.usage.input_tokens,
          output: data.usage.output_tokens,
        },
      };
    },
  };

  return { provider, model };
}

async function cmdJudge(_dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      prompt: { type: "string", short: "p" },
      output: { type: "string", short: "o" },
      rubric: { type: "string", short: "r" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help || !values.prompt || !values.output || !values.rubric) {
    console.log("autoctx judge -p <task-prompt> -o <agent-output> -r <rubric>");
    process.exit(values.help ? 0 : 1);
  }

  const { provider, model } = await getProvider();
  const { LLMJudge } = await import("../judge/index.js");

  const judge = new LLMJudge({ provider, model, rubric: values.rubric });
  const result = await judge.evaluate({
    taskPrompt: values.prompt,
    agentOutput: values.output,
  });

  console.log(JSON.stringify({
    score: result.score,
    reasoning: result.reasoning,
    dimensionScores: result.dimensionScores,
  }, null, 2));
}

async function cmdImprove(_dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      prompt: { type: "string", short: "p" },
      output: { type: "string", short: "o" },
      rubric: { type: "string", short: "r" },
      rounds: { type: "string", short: "n", default: "5" },
      threshold: { type: "string", short: "t", default: "0.9" },
      "min-rounds": { type: "string", default: "1" },
      verbose: { type: "boolean", short: "v" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help || !values.prompt || !values.output || !values.rubric) {
    console.log("autoctx improve -p <task-prompt> -o <initial-output> -r <rubric> [-n rounds] [-t threshold] [--min-rounds N] [-v]");
    process.exit(values.help ? 0 : 1);
  }

  const { provider, model } = await getProvider();
  const { SimpleAgentTask } = await import("../execution/task-runner.js");
  const { ImprovementLoop } = await import("../execution/improvement-loop.js");

  const task = new SimpleAgentTask(values.prompt, values.rubric, provider, model);
  const loop = new ImprovementLoop({
    task,
    maxRounds: parseInt(values.rounds ?? "5", 10),
    qualityThreshold: parseFloat(values.threshold ?? "0.9"),
    minRounds: parseInt(values["min-rounds"] ?? "1", 10),
  });

  const startTime = performance.now();
  const result = await loop.run({ initialOutput: values.output, state: {} });
  const durationMs = Math.round(performance.now() - startTime);

  if (values.verbose) {
    for (const round of result.rounds) {
      console.error(JSON.stringify({
        round: round.roundNumber,
        score: round.score,
        dimensionScores: round.dimensionScores,
        reasoning: round.reasoning.length > 200 ? round.reasoning.slice(0, 200) + "..." : round.reasoning,
        isRevision: round.isRevision,
        judgeFailed: round.judgeFailed,
      }));
    }
  }

  console.log(JSON.stringify({
    totalRounds: result.totalRounds,
    metThreshold: result.metThreshold,
    bestScore: result.bestScore,
    bestRound: result.bestRound,
    judgeFailures: result.judgeFailures,
    terminationReason: result.terminationReason,
    totalInternalRetries: result.totalInternalRetries,
    dimensionTrajectory: result.dimensionTrajectory,
    bestOutput: result.bestOutput,
    durationMs,
  }, null, 2));
}

async function cmdQueue(dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      spec: { type: "string", short: "s" },
      prompt: { type: "string", short: "p" },
      rubric: { type: "string", short: "r" },
      priority: { type: "string", default: "0" },
      "min-rounds": { type: "string" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help || !values.spec) {
    console.log("autoctx queue -s <spec-name> [-p prompt] [-r rubric] [--priority N] [--min-rounds N]");
    process.exit(values.help ? 0 : 1);
  }

  const { SQLiteStore } = await import("../storage/index.js");
  const { enqueueTask } = await import("../execution/task-runner.js");

  const store = new SQLiteStore(dbPath);
  const migrationsDir = getMigrationsDir();
  store.migrate(migrationsDir);

  const id = enqueueTask(store, values.spec, {
    taskPrompt: values.prompt,
    rubric: values.rubric,
    priority: parseInt(values.priority!, 10),
    ...(values["min-rounds"] ? { minRounds: parseInt(values["min-rounds"], 10) } : {}),
  });

  console.log(JSON.stringify({ taskId: id, specName: values.spec, status: "queued" }));
  store.close();
}

async function cmdStatus(dbPath: string): Promise<void> {
  const { SQLiteStore } = await import("../storage/index.js");
  const store = new SQLiteStore(dbPath);

  try {
    const migrationsDir = getMigrationsDir();
    store.migrate(migrationsDir);
    const pending = store.pendingTaskCount();
    console.log(JSON.stringify({ pendingCount: pending }));
  } finally {
    store.close();
  }
}

async function cmdServe(dbPath: string): Promise<void> {
  const { SQLiteStore } = await import("../storage/index.js");
  const { startServer } = await import("../mcp/server.js");

  const store = new SQLiteStore(dbPath);
  const migrationsDir = getMigrationsDir();
  store.migrate(migrationsDir);

  const { provider, model } = await getProvider();

  await startServer({ store, provider, model });
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
