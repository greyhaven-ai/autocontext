import { resolve } from "node:path";

import { buildContextSelectionReport } from "../knowledge/context-selection-report.js";
import { loadContextSelectionDecisions } from "../knowledge/context-selection-store.js";

export const CONTEXT_SELECTION_HELP_TEXT = `autoctx context-selection — Inspect persisted context-selection telemetry

Usage:
  autoctx context-selection --run-id <run-id> [--json]
  autoctx context-selection <run-id> [--json]

Options:
  --run-id <id>        Run id to inspect
  --json              Output as JSON
  -h, --help          Show this help

Examples:
  autoctx context-selection --run-id run-123
  autoctx context-selection --run-id run-123 --json`;

export interface ContextSelectionCommandPlan {
  runId: string;
  json: boolean;
}

export interface ContextSelectionCommandResult {
  exitCode: number;
  stdout: string;
  stderr: string;
}

export function planContextSelectionCommand(
  values: { "run-id"?: string; json?: boolean },
  positionals: string[],
): ContextSelectionCommandPlan {
  const runId = String(values["run-id"] ?? positionals[0] ?? "").trim();
  if (!runId) {
    throw new Error("Error: context-selection requires --run-id <run-id>");
  }
  const extra = positionals.slice(values["run-id"] ? 0 : 1);
  if (extra.length > 0) {
    throw new Error(`Unexpected context-selection arguments: ${extra.join(" ")}`);
  }
  return {
    runId,
    json: values.json === true,
  };
}

export function executeContextSelectionCommandWorkflow(opts: {
  runsRoot: string;
  plan: ContextSelectionCommandPlan;
}): ContextSelectionCommandResult {
  const runsRoot = resolve(opts.runsRoot);
  let decisions;
  try {
    decisions = loadContextSelectionDecisions(runsRoot, opts.plan.runId);
  } catch (error) {
    return renderFailure(opts.plan.runId, errorMessage(error), opts.plan.json);
  }
  if (decisions.length === 0) {
    return renderFailure(
      opts.plan.runId,
      `No context selection artifacts found for '${opts.plan.runId}'`,
      opts.plan.json,
    );
  }
  const report = buildContextSelectionReport(decisions);
  return {
    exitCode: 0,
    stdout: opts.plan.json
      ? JSON.stringify(report.toDict(), null, 2)
      : report.toMarkdown(),
    stderr: "",
  };
}

function renderFailure(runId: string, error: string, json: boolean): ContextSelectionCommandResult {
  return {
    exitCode: 1,
    stdout: json ? JSON.stringify({ status: "failed", error, run_id: runId }, null, 2) : "",
    stderr: json ? "" : error,
  };
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
