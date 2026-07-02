/**
 * `simulate`, `investigate`, `analyze`, `context-selection` command family
 * (AC-853 split of command-handlers.ts).
 */
import { parseArgs } from "node:util";
import { emitEngineResult } from "../emit-engine-result.js";
import type { LLMProvider } from "../../types/index.js";
import { errorMessage, getProvider } from "./shared.js";

// ---------------------------------------------------------------------------
// simulate command (AC-446)
// ---------------------------------------------------------------------------

export async function cmdSimulate(): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      description: { type: "string", short: "d" },
      replay: { type: "string" },
      "compare-left": { type: "string" },
      "compare-right": { type: "string" },
      export: { type: "string" },
      format: { type: "string" },
      "sweep-file": { type: "string" },
      preset: { type: "string" },
      "preset-file": { type: "string" },
      variables: { type: "string" },
      sweep: { type: "string" },
      runs: { type: "string" },
      "max-steps": { type: "string" },
      "save-as": { type: "string" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const {
    executeSimulateCompareWorkflow,
    executeSimulateExportWorkflow,
    executeSimulateReplayWorkflow,
    executeSimulateRunWorkflow,
    SIMULATE_HELP_TEXT,
    planSimulateCommand,
    planSimulateInputs,
    renderCompareSuccess,
    renderReplaySuccess,
    renderSimulationSuccess,
  } = await import("../simulate-command-workflow.js");

  if (values.help) {
    console.log(SIMULATE_HELP_TEXT);
    process.exit(0);
  }

  let plan;
  try {
    plan = planSimulateCommand(values);
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  const { SimulationEngine, parseVariableOverrides, parseSweepSpec } =
    await import("../../simulation/engine.js");
  const { loadSettings } = await import("../../config/index.js");
  const { resolve } = await import("node:path");
  const settings = loadSettings();

  // Export mode (AC-452)
  if (plan.mode === "export") {
    const { exportSimulation } = await import("../../simulation/export.js");
    try {
      console.log(
        executeSimulateExportWorkflow({
          exportId: plan.exportId!,
          format: values.format,
          knowledgeRoot: resolve(settings.knowledgeRoot),
          json: !!values.json,
          exportSimulation,
        }),
      );
    } catch (error) {
      console.error(errorMessage(error));
      process.exit(1);
    }
    return;
  }

  // Compare mode (AC-451)
  if (plan.mode === "compare") {
    const result = await executeSimulateCompareWorkflow({
      compareLeft: plan.compareLeft!,
      compareRight: plan.compareRight!,
      knowledgeRoot: resolve(settings.knowledgeRoot),
      createEngine: (provider, knowledgeRoot) =>
        new SimulationEngine(provider as unknown as LLMProvider, knowledgeRoot),
    });
    emitEngineResult(result, {
      json: !!values.json,
      label: "Compare",
      renderSuccess: (r) => {
        console.log(renderCompareSuccess(r));
      },
    });
    return;
  }

  // Replay mode (AC-450)
  if (plan.mode === "replay") {
    const result = await executeSimulateReplayWorkflow({
      replayId: plan.replayId!,
      knowledgeRoot: resolve(settings.knowledgeRoot),
      variables: values.variables,
      maxSteps: values["max-steps"],
      createEngine: (provider, knowledgeRoot) =>
        new SimulationEngine(provider as unknown as LLMProvider, knowledgeRoot),
      parseVariableOverrides,
    });
    emitEngineResult(result, {
      json: !!values.json,
      label: "Replay",
      renderSuccess: (r) => {
        console.log(renderReplaySuccess(r));
      },
    });
    return;
  }

  // Build sweep from --sweep or --sweep-file, and variables from --variables/--preset (AC-454)
  const { loadSweepFile, parsePreset } = await import("../../simulation/sweep-dsl.js");
  const { readFileSync: readFile } = await import("node:fs");

  let sweep;
  let variables;
  try {
    const inputPlan = await planSimulateInputs({
      values,
      parseSweepSpec,
      loadSweepFile,
      parseVariableOverrides,
      readPresetFile: (path) => readFile(path, "utf-8"),
      parsePreset,
    });
    sweep = inputPlan.sweep;
    variables = inputPlan.variables;
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  const { provider } = await getProvider();

  try {
    const result = await executeSimulateRunWorkflow({
      description: plan.description!,
      provider,
      knowledgeRoot: resolve(settings.knowledgeRoot),
      variables,
      sweep,
      runs: values.runs,
      maxSteps: values["max-steps"],
      saveAs: values["save-as"],
      createEngine: (runProvider, knowledgeRoot) =>
        new SimulationEngine(runProvider, knowledgeRoot),
    });

    emitEngineResult(result, {
      json: !!values.json,
      label: "Simulation",
      renderSuccess: (r) => {
        console.log(renderSimulationSuccess(r));
      },
    });
  } finally {
    provider.close?.();
  }
}

// ---------------------------------------------------------------------------
// investigate command (AC-447)
// ---------------------------------------------------------------------------

export async function cmdInvestigate(): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      description: { type: "string", short: "d" },
      "max-steps": { type: "string" },
      hypotheses: { type: "string" },
      "save-as": { type: "string" },
      "browser-url": { type: "string" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const {
    INVESTIGATE_HELP_TEXT,
    executeInvestigateCommandWorkflow,
    prepareInvestigateRequest,
    renderInvestigationSuccess,
  } = await import("../investigate-command-workflow.js");

  if (values.help) {
    console.log(INVESTIGATE_HELP_TEXT);
    process.exit(0);
  }

  const { InvestigationEngine } = await import("../../investigation/engine.js");
  const { loadSettings } = await import("../../config/index.js");
  const { resolve } = await import("node:path");

  const { provider } = await getProvider();

  const settings = loadSettings();
  const engine = new InvestigationEngine(provider, resolve(settings.knowledgeRoot));

  let request;
  let result;
  try {
    request = await prepareInvestigateRequest({ values, settings });
    result = await executeInvestigateCommandWorkflow({ values, request, engine });
  } catch (error) {
    console.error(errorMessage(error));
    provider.close?.();
    process.exit(1);
  }

  try {
    emitEngineResult(result, {
      json: !!values.json,
      label: "Investigation",
      renderSuccess: (r) => {
        console.log(renderInvestigationSuccess(r));
      },
    });
  } finally {
    provider.close?.();
  }
}

// ---------------------------------------------------------------------------
// analyze command (AC-448)
// ---------------------------------------------------------------------------

export async function cmdAnalyze(): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      id: { type: "string" },
      type: { type: "string" },
      left: { type: "string" },
      right: { type: "string" },
      focus: { type: "string" },
      "save-report": { type: "boolean" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help) {
    console.log(`autoctx analyze — analyze and compare artifacts

Usage:
  autoctx analyze --id <artifact-id> --type <run|simulation|investigation|mission>
  autoctx analyze --left <id> --right <id> --type <type>

Options:
  --id <id>            Artifact to analyze (single mode)
  --left <id>          Left artifact for comparison
  --right <id>         Right artifact for comparison
  --type <type>        Artifact type: run, simulation, investigation, mission
  --focus <area>       Focus area: regression, sensitivity, attribution
  --json               Output as JSON
  -h, --help           Show this help

Examples:
  autoctx analyze --id deploy_sim --type simulation --json
  autoctx analyze --left sim_a --right sim_b --type simulation
  autoctx analyze --id checkout_rca --type investigation`);
    process.exit(0);
  }

  const { AnalysisEngine } = await import("../../analysis/engine.js");
  const { loadSettings } = await import("../../config/index.js");
  const { resolve } = await import("node:path");

  const settings = loadSettings();
  const engine = new AnalysisEngine({
    knowledgeRoot: resolve(settings.knowledgeRoot),
    runsRoot: resolve(settings.runsRoot),
    dbPath: resolve(settings.dbPath),
  });
  const type = (values.type ?? "simulation") as "run" | "simulation" | "investigation" | "mission";

  let result;
  if (values.left && values.right) {
    result = engine.compare({
      left: { id: values.left, type },
      right: { id: values.right, type },
      focus: values.focus,
    });
  } else if (values.id) {
    result = engine.analyze({ id: values.id, type, focus: values.focus });
  } else {
    console.error("Error: --id or --left/--right required. Run 'autoctx analyze --help'.");
    process.exit(1);
  }

  if (values.json) {
    console.log(JSON.stringify(result, null, 2));
  } else {
    console.log(`Analysis: ${result.summary.headline}`);
    console.log(`Confidence: ${result.summary.confidence.toFixed(2)}`);
    if (result.findings.length > 0) {
      console.log(`\nFindings:`);
      for (const f of result.findings) {
        const icon =
          f.kind === "improvement"
            ? "↑"
            : f.kind === "regression"
              ? "↓"
              : f.kind === "conclusion"
                ? "→"
                : "•";
        console.log(`  ${icon} [${f.kind}] ${f.statement}`);
      }
    }
    if (result.regressions.length > 0) {
      console.log(`\nRegressions:`);
      for (const reg of result.regressions) console.log(`  ↓ ${reg}`);
    }
    if (result.attribution) {
      console.log(`\nAttribution:`);
      for (const f of result.attribution.topFactors)
        console.log(`  ${f.name}: ${f.weight.toFixed(2)}`);
    }
    if (result.limitations.length > 0) {
      console.log(`\nLimitations:`);
      for (const l of result.limitations) console.log(`  ⚠ ${l}`);
    }
  }
}

export async function cmdContextSelection(): Promise<void> {
  const { values, positionals } = parseArgs({
    args: process.argv.slice(3),
    allowPositionals: true,
    options: {
      "run-id": { type: "string" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });
  const {
    CONTEXT_SELECTION_HELP_TEXT,
    executeContextSelectionCommandWorkflow,
    planContextSelectionCommand,
  } = await import("../context-selection-command-workflow.js");

  if (values.help) {
    console.log(CONTEXT_SELECTION_HELP_TEXT);
    process.exit(0);
  }

  let plan;
  try {
    plan = planContextSelectionCommand(values, positionals);
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  const { loadSettings } = await import("../../config/index.js");
  const { resolve } = await import("node:path");
  const settings = loadSettings();
  const result = executeContextSelectionCommandWorkflow({
    runsRoot: resolve(settings.runsRoot),
    plan,
  });
  if (result.stdout) {
    console.log(result.stdout);
  }
  if (result.stderr) {
    console.error(result.stderr);
  }
  if (result.exitCode !== 0) {
    process.exit(result.exitCode);
  }
}
