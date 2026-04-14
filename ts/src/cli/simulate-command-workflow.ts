import type { SimulationCompareResult, SimulationResult } from "../simulation/types.js";
import type { ExportFormat, SimulationExportResult } from "../simulation/export.js";
import type { SweepDimension } from "../simulation/sweep-dsl.js";

export const SIMULATE_HELP_TEXT = `autoctx simulate — run a plain-language simulation

Usage:
  autoctx simulate --description "..." [options]
  autoctx simulate --replay <id> [--variables ...] [--max-steps N]
  autoctx simulate --compare-left <id> --compare-right <id>

Options:
  -d, --description <text>   Plain-language description of what to simulate
  --replay <id>              Replay a previously saved simulation
  --compare-left <id>        Left simulation for comparison
  --compare-right <id>       Right simulation for comparison
  --export <id>              Export a saved simulation as a portable package
  --format <fmt>             Export format: json (default), markdown, csv
  --sweep-file <path>        Load sweep config from JSON file
  --preset <name>            Apply a named variable preset
  --preset-file <path>       JSON file with named presets
  --variables <key=val,...>   Variable overrides (e.g., threshold=0.7,budget=100)
  --sweep <key=min:max:step>  Parameter sweep (e.g., threshold=0.4:0.9:0.1)
  --runs <N>                  Number of runs (default: 1, or determined by sweep)
  --max-steps <N>             Maximum steps per run (default: 20)
  --save-as <name>            Name for the saved simulation
  --json                      Output as JSON
  -h, --help                  Show this help

Examples:
  autoctx simulate -d "simulate deploying a web service with rollback"
  autoctx simulate -d "simulate a pricing war" --variables max_steps=12
  autoctx simulate --replay deploy_sim
  autoctx simulate --replay deploy_sim --variables threshold=0.9 --json
  autoctx simulate --compare-left sim_a --compare-right sim_b --json
  autoctx simulate --export deploy_sim --format markdown
  autoctx simulate --export deploy_sim --format csv`;

export interface SimulateCommandValues {
  description?: string;
  replay?: string;
  export?: string;
  "compare-left"?: string;
  "compare-right"?: string;
  preset?: string;
  "preset-file"?: string;
  variables?: string;
  sweep?: string;
  "sweep-file"?: string;
}

export interface SimulateInputPlan {
  sweep?: SweepDimension[];
  variables?: Record<string, unknown>;
}

export interface SimulateCommandPlan {
  mode: "run" | "replay" | "compare" | "export";
  description?: string;
  replayId?: string;
  exportId?: string;
  compareLeft?: string;
  compareRight?: string;
}

export function planSimulateCommand(values: SimulateCommandValues): SimulateCommandPlan {
  const hasCompareLeft = typeof values["compare-left"] === "string" && values["compare-left"].length > 0;
  const hasCompareRight = typeof values["compare-right"] === "string" && values["compare-right"].length > 0;
  const hasExport = typeof values.export === "string" && values.export.length > 0;

  if (hasCompareLeft !== hasCompareRight) {
    throw new Error(
      "Error: --compare-left and --compare-right must be provided together. Run 'autoctx simulate --help' for usage.",
    );
  }

  if (!values.description && !values.replay && !hasCompareLeft && !hasExport) {
    throw new Error(
      "Error: --description, --replay, --compare-left/--compare-right, or --export is required. Run 'autoctx simulate --help' for usage.",
    );
  }

  if (hasExport) {
    return { mode: "export", exportId: values.export };
  }
  if (hasCompareLeft && hasCompareRight) {
    return {
      mode: "compare",
      compareLeft: values["compare-left"],
      compareRight: values["compare-right"],
    };
  }
  if (values.replay) {
    return { mode: "replay", replayId: values.replay };
  }
  return { mode: "run", description: values.description };
}

export function ensurePresetPairing(values: Pick<SimulateCommandValues, "preset" | "preset-file">): void {
  const hasPreset = typeof values.preset === "string" && values.preset.length > 0;
  const hasPresetFile = typeof values["preset-file"] === "string" && values["preset-file"].length > 0;
  if (hasPreset !== hasPresetFile) {
    throw new Error(
      "Error: --preset and --preset-file must be provided together. Run 'autoctx simulate --help' for usage.",
    );
  }
}

export async function planSimulateInputs(opts: {
  values: Pick<SimulateCommandValues, "sweep" | "sweep-file" | "variables" | "preset" | "preset-file" | "description">;
  parseSweepSpec: (raw: string) => SweepDimension[];
  loadSweepFile: (path: string) => SweepDimension[];
  parseVariableOverrides: (raw: string) => Record<string, unknown>;
  readPresetFile: (path: string) => string;
  parsePreset: (preset: string, raw: string) => Record<string, unknown> | null;
}): Promise<SimulateInputPlan> {
  ensurePresetPairing(opts.values);

  let sweep = opts.values.sweep ? opts.parseSweepSpec(opts.values.sweep) : undefined;
  if (!sweep && opts.values["sweep-file"]) {
    sweep = opts.loadSweepFile(opts.values["sweep-file"]);
  }

  let variables = opts.values.variables
    ? opts.parseVariableOverrides(opts.values.variables)
    : undefined;

  if (opts.values.preset && opts.values["preset-file"]) {
    const presetVars = opts.parsePreset(
      opts.values.preset,
      opts.readPresetFile(opts.values["preset-file"]),
    );
    if (!presetVars) {
      throw new Error(
        `Error: preset '${opts.values.preset}' was not found or '${opts.values["preset-file"]}' is not valid preset JSON.`,
      );
    }
    variables = { ...presetVars, ...(variables ?? {}) };
  }

  return { sweep, variables };
}

export function createCompareProvider(): { name: string } {
  return { name: "local-compare" };
}

export function createReplayProvider(): { name: string } {
  return { name: "local-replay" };
}

export async function executeSimulateCompareWorkflow<TResult>(opts: {
  compareLeft: string;
  compareRight: string;
  knowledgeRoot: string;
  createEngine: (provider: { name: string }, knowledgeRoot: string) => {
    compare(request: { left: string; right: string }): Promise<TResult>;
  };
}): Promise<TResult> {
  const engine = opts.createEngine(createCompareProvider(), opts.knowledgeRoot);
  return engine.compare({ left: opts.compareLeft, right: opts.compareRight });
}

export async function executeSimulateReplayWorkflow<TResult>(opts: {
  replayId: string;
  knowledgeRoot: string;
  variables?: string;
  maxSteps?: string;
  createEngine: (provider: { name: string }, knowledgeRoot: string) => {
    replay(request: { id: string; variables?: Record<string, unknown>; maxSteps?: number }): Promise<TResult>;
  };
  parseVariableOverrides: (raw: string) => Record<string, unknown>;
}): Promise<TResult> {
  const engine = opts.createEngine(createReplayProvider(), opts.knowledgeRoot);
  return engine.replay({
    id: opts.replayId,
    variables: opts.variables ? opts.parseVariableOverrides(opts.variables) : undefined,
    maxSteps: opts.maxSteps ? Number.parseInt(opts.maxSteps, 10) : undefined,
  });
}

export async function executeSimulateRunWorkflow<TResult, TProvider>(opts: {
  description: string;
  provider: TProvider;
  knowledgeRoot: string;
  variables?: Record<string, unknown>;
  sweep?: SweepDimension[];
  runs?: string;
  maxSteps?: string;
  saveAs?: string;
  createEngine: (provider: TProvider, knowledgeRoot: string) => {
    run(request: {
      description: string;
      variables?: Record<string, unknown>;
      sweep?: SweepDimension[];
      runs?: number;
      maxSteps?: number;
      saveAs?: string;
    }): Promise<TResult>;
  };
}): Promise<TResult> {
  const engine = opts.createEngine(opts.provider, opts.knowledgeRoot);
  return engine.run({
    description: opts.description,
    variables: opts.variables,
    sweep: opts.sweep,
    runs: opts.runs ? Number.parseInt(opts.runs, 10) : undefined,
    maxSteps: opts.maxSteps ? Number.parseInt(opts.maxSteps, 10) : undefined,
    saveAs: opts.saveAs,
  });
}

export function executeSimulateExportWorkflow(opts: {
  exportId: string;
  format: string | undefined;
  knowledgeRoot: string;
  json: boolean;
  exportSimulation: (request: {
    id: string;
    knowledgeRoot: string;
    format: ExportFormat;
  }) => SimulationExportResult;
}): string {
  if (opts.format && !["json", "markdown", "csv"].includes(opts.format)) {
    throw new Error(
      `Export failed: Unsupported export format '${opts.format}'. Use json, markdown, or csv.`,
    );
  }

  const format = (opts.format ?? "json") as ExportFormat;
  const result = opts.exportSimulation({
    id: opts.exportId,
    knowledgeRoot: opts.knowledgeRoot,
    format,
  });

  if (result.status === "failed") {
    throw new Error(`Export failed: ${result.error}`);
  }

  if (opts.json) {
    return JSON.stringify(result, null, 2);
  }
  return `Exported: ${result.outputPath}`;
}

export function renderSimulationSuccess(result: SimulationResult): string {
  const lines = [
    `Simulation: ${result.name} (family: ${result.family})`,
    `Score: ${result.summary.score}`,
    `Reasoning: ${result.summary.reasoning}`,
  ];
  if (result.sweep) {
    lines.push(`Sweep: ${result.sweep.runs} runs across ${result.sweep.dimensions.length} dimension(s)`);
  }
  if (result.summary.mostSensitiveVariables?.length) {
    lines.push(`Most sensitive: ${result.summary.mostSensitiveVariables.join(", ")}`);
  }
  lines.push("", "Assumptions:");
  for (const assumption of result.assumptions) lines.push(`  - ${assumption}`);
  lines.push("", "Warnings:");
  for (const warning of result.warnings) lines.push(`  ⚠ ${warning}`);
  lines.push("", `Artifacts: ${result.artifacts.scenarioDir}`);
  return lines.join("\n");
}

export function renderReplaySuccess(result: SimulationResult): string {
  return [
    `Replay: ${result.name} (original score: ${result.originalScore?.toFixed(2)}, replay score: ${result.summary.score.toFixed(2)}, delta: ${result.scoreDelta?.toFixed(4)})`,
    `Artifacts: ${result.artifacts.scenarioDir}`,
  ].join("\n");
}

export function renderCompareSuccess(result: SimulationCompareResult): string {
  const lines = [
    `Compare: ${result.left.name} vs ${result.right.name}`,
    `Score: ${result.left.score.toFixed(2)} → ${result.right.score.toFixed(2)} (delta: ${result.scoreDelta.toFixed(4)})`,
  ];
  if (result.likelyDrivers.length > 0) {
    lines.push(`Likely drivers: ${result.likelyDrivers.join(", ")}`);
  }
  lines.push(result.summary);
  return lines.join("\n");
}
