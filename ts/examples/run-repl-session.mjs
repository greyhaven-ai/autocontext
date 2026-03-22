#!/usr/bin/env node

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const THIS_DIR = dirname(fileURLToPath(import.meta.url));
const TS_ROOT = join(THIS_DIR, "..");

const HELP = `
run-repl-session.mjs — example MCP client for AutoContext's run_repl_session tool

Usage:
  node examples/run-repl-session.mjs [options]

Options:
  --prompt TEXT               Task prompt to run through the REPL session
  --rubric TEXT               Evaluation rubric
  --phase generate|revise     Session phase (default: generate)
  --output TEXT               Current output for revise mode
  --reference-context TEXT    Authoritative context for the session
  --required-concept TEXT     Repeatable required concept
  --model TEXT                Optional RLM model override
  --turns N                   Max REPL turns (default: 4)
  --max-tokens N              Per-turn token cap (default: 2048)
  --temperature N             Sampling temperature (default: 0.2)
  --max-stdout N              Stdout cap per turn (default: 8192)
  --timeout-ms N              Code timeout in milliseconds (default: 10000)
  --memory-mb N               Memory cap in MB (default: 64)
  --help                      Show this help

This script spawns the local AutoContext TypeScript MCP server over stdio:
  npx tsx src/cli/index.ts serve

Requirements:
  - run from the repo with dependencies installed in ts/
  - set ANTHROPIC_API_KEY (and optionally AUTOCONTEXT_MODEL) for the server process
`.trim();

function parseArgs(argv) {
  const values = {
    prompt: "Write a concise summary of what AutoContext does.",
    rubric: "Reward clarity, factual accuracy, and completeness.",
    phase: "generate",
    currentOutput: undefined,
    referenceContext: undefined,
    requiredConcepts: [],
    model: undefined,
    turns: 4,
    maxTokens: 2048,
    temperature: 0.2,
    maxStdout: 8192,
    timeoutMs: 10000,
    memoryMb: 64,
    help: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const next = argv[index + 1];
    switch (arg) {
      case "--prompt":
        values.prompt = next;
        index += 1;
        break;
      case "--rubric":
        values.rubric = next;
        index += 1;
        break;
      case "--phase":
        values.phase = next;
        index += 1;
        break;
      case "--output":
        values.currentOutput = next;
        index += 1;
        break;
      case "--reference-context":
        values.referenceContext = next;
        index += 1;
        break;
      case "--required-concept":
        values.requiredConcepts.push(next);
        index += 1;
        break;
      case "--model":
        values.model = next;
        index += 1;
        break;
      case "--turns":
        values.turns = Number.parseInt(next, 10);
        index += 1;
        break;
      case "--max-tokens":
        values.maxTokens = Number.parseInt(next, 10);
        index += 1;
        break;
      case "--temperature":
        values.temperature = Number.parseFloat(next);
        index += 1;
        break;
      case "--max-stdout":
        values.maxStdout = Number.parseInt(next, 10);
        index += 1;
        break;
      case "--timeout-ms":
        values.timeoutMs = Number.parseInt(next, 10);
        index += 1;
        break;
      case "--memory-mb":
        values.memoryMb = Number.parseInt(next, 10);
        index += 1;
        break;
      case "--help":
      case "-h":
        values.help = true;
        break;
      default:
        throw new Error(`Unknown argument: ${arg}`);
    }
  }

  return values;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));

  if (args.help) {
    console.log(HELP);
    return;
  }

  if (args.phase === "revise" && !args.currentOutput) {
    throw new Error("--output is required when --phase revise");
  }

  if (!process.env.ANTHROPIC_API_KEY) {
    throw new Error("ANTHROPIC_API_KEY must be set before running this example");
  }

  const transport = new StdioClientTransport({
    command: "npx",
    args: ["tsx", "src/cli/index.ts", "serve"],
    cwd: TS_ROOT,
    env: {
      ...process.env,
      NODE_NO_WARNINGS: "1",
    },
    stderr: "inherit",
  });

  const client = new Client({
    name: "autoctx-repl-example",
    version: "0.1.0",
  });

  try {
    await client.connect(transport);

    const tools = await client.listTools();
    if (!tools.tools.some((tool) => tool.name === "run_repl_session")) {
      throw new Error("run_repl_session tool is not available on the server");
    }

    const result = await client.callTool({
      name: "run_repl_session",
      arguments: {
        taskPrompt: args.prompt,
        rubric: args.rubric,
        phase: args.phase,
        ...(args.currentOutput ? { currentOutput: args.currentOutput } : {}),
        ...(args.referenceContext ? { referenceContext: args.referenceContext } : {}),
        ...(args.requiredConcepts.length > 0 ? { requiredConcepts: args.requiredConcepts } : {}),
        ...(args.model ? { rlmModel: args.model } : {}),
        rlmMaxTurns: args.turns,
        rlmMaxTokensPerTurn: args.maxTokens,
        rlmTemperature: args.temperature,
        rlmMaxStdoutChars: args.maxStdout,
        rlmCodeTimeoutMs: args.timeoutMs,
        rlmMemoryLimitMb: args.memoryMb,
      },
    });

    const textPart = result.content.find((part) => part.type === "text");
    if (!textPart) {
      throw new Error("run_repl_session returned no text content");
    }

    const payload = JSON.parse(textPart.text);
    console.log(JSON.stringify(payload, null, 2));
  } finally {
    await client.close();
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
