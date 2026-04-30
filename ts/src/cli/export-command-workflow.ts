import { mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

export const EXPORT_HELP_TEXT = `autoctx export — Export strategy package for a run or scenario

Usage:
  autoctx export <run-id> [--output <file>] [--json]
  autoctx export --scenario <name> [--output <file>] [--json]

Options:
  <run-id>             Run to export as a strategy package
  --run-id <id>        Same run id as a named option
  --scenario <name>    Scenario to export
  --output <file>      Output file path (default: stdout)
  --json               Force JSON output format

See also: import-package, run, replay`;

export interface ExportCommandValues {
  scenario?: string;
  "run-id"?: string;
  positionals?: string[];
  output?: string;
  json?: boolean;
}

export interface ExportCommandPlan {
  scenarioName: string;
  runId?: string;
  output?: string;
  json: boolean;
}

export async function planExportCommand(
  values: ExportCommandValues,
  resolveScenarioOption: (scenario: string | undefined) => Promise<string | undefined>,
  resolveRunScenario: (runId: string) => Promise<string | undefined>,
): Promise<ExportCommandPlan> {
  const explicitScenario = values.scenario?.trim();
  if (explicitScenario) {
    const scenarioName = await resolveScenarioOption(explicitScenario);
    if (!scenarioName) {
      throw new Error("Error: --scenario or <run-id> is required");
    }
    const explicitRunId = values["run-id"]?.trim();
    if (explicitRunId) {
      const runScenario = await resolveRunScenario(explicitRunId);
      if (!runScenario) {
        throw new Error(`Error: run '${explicitRunId}' not found`);
      }
      if (runScenario !== scenarioName) {
        throw new Error(
          `Error: run '${explicitRunId}' belongs to scenario '${runScenario}', not '${scenarioName}'`,
        );
      }
      return {
        scenarioName,
        runId: explicitRunId,
        output: values.output,
        json: !!values.json,
      };
    }
    return {
      scenarioName,
      runId: undefined,
      output: values.output,
      json: !!values.json,
    };
  }

  const runId = values["run-id"]?.trim() || values.positionals?.[0]?.trim();
  if (runId) {
    const scenarioName = await resolveRunScenario(runId);
    if (!scenarioName) {
      throw new Error(`Error: run '${runId}' not found`);
    }
    return {
      scenarioName,
      runId,
      output: values.output,
      json: !!values.json,
    };
  }

  throw new Error("Error: --scenario or <run-id> is required");
}

function writeOutputFileWithParents(path: string, content: string): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, content, "utf-8");
}

export function executeExportCommandWorkflow<
  TResult extends Record<string, unknown>,
  TArtifacts,
  TStore,
>(opts: {
  scenarioName: string;
  runId?: string;
  output?: string;
  json?: boolean;
  exportStrategyPackage: (args: {
    scenarioName: string;
    sourceRunId?: string;
    artifacts: TArtifacts;
    store: TStore;
  }) => TResult;
  artifacts: TArtifacts;
  store: TStore;
  writeOutputFile?: (path: string, content: string) => void;
}): string {
  const result = opts.exportStrategyPackage({
    scenarioName: opts.scenarioName,
    ...(opts.runId ? { sourceRunId: opts.runId } : {}),
    artifacts: opts.artifacts,
    store: opts.store,
  });
  const serialized = `${JSON.stringify(result, null, 2)}\n`;

  if (!opts.output) {
    return serialized.trimEnd();
  }

  const writeOutputFile = opts.writeOutputFile ?? writeOutputFileWithParents;
  writeOutputFile(opts.output, serialized);
  if (opts.json) {
    return JSON.stringify({ output: opts.output });
  }
  return `Exported to ${opts.output}`;
}
