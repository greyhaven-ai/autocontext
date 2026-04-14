import { mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

export const EXPORT_HELP_TEXT = `autoctx export — Export strategy package for a scenario

Usage: autoctx export [options]

Options:
  --scenario <name>    Scenario to export (required)
  --output <file>      Output file path (default: stdout)
  --json               Force JSON output format

See also: import-package, run, replay`;

export interface ExportCommandValues {
  scenario?: string;
  output?: string;
  json?: boolean;
}

export interface ExportCommandPlan {
  scenarioName: string;
  output?: string;
  json: boolean;
}

export async function planExportCommand(
  values: ExportCommandValues,
  resolveScenarioOption: (scenario: string | undefined) => Promise<string | undefined>,
): Promise<ExportCommandPlan> {
  const scenarioName = await resolveScenarioOption(values.scenario);
  if (!scenarioName) {
    throw new Error("Error: --scenario is required");
  }
  return {
    scenarioName,
    output: values.output,
    json: !!values.json,
  };
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
  output?: string;
  json?: boolean;
  exportStrategyPackage: (args: {
    scenarioName: string;
    artifacts: TArtifacts;
    store: TStore;
  }) => TResult;
  artifacts: TArtifacts;
  store: TStore;
  writeOutputFile?: (path: string, content: string) => void;
}): string {
  const result = opts.exportStrategyPackage({
    scenarioName: opts.scenarioName,
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
