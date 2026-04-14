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
  resolveScenarioOption: (explicit?: string) => Promise<string | undefined>,
): Promise<ExportCommandPlan> {
  const scenarioName = await resolveScenarioOption(values.scenario);
  if (!scenarioName) {
    throw new Error("Error: --scenario is required");
  }
  return {
    scenarioName,
    output: values.output,
    json: Boolean(values.json),
  };
}

function writeOutputFileWithParents(path: string, contents: string): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, contents, "utf-8");
}

export function executeExportCommandWorkflow<Store, Artifacts>(opts: {
  scenarioName: string;
  output?: string;
  json?: boolean;
  store: Store;
  artifacts: Artifacts;
  exportStrategyPackage: (request: {
    scenarioName: string;
    artifacts: Artifacts;
    store: Store;
  }) => unknown;
  writeOutputFile?: (path: string, contents: string) => void;
}): string {
  const result = opts.exportStrategyPackage({
    scenarioName: opts.scenarioName,
    artifacts: opts.artifacts,
    store: opts.store,
  });

  if (!opts.output) {
    return JSON.stringify(result, null, 2);
  }

  const writeOutputFile = opts.writeOutputFile ?? writeOutputFileWithParents;
  writeOutputFile(opts.output, `${JSON.stringify(result, null, 2)}\n`);
  return opts.json
    ? JSON.stringify({ output: opts.output })
    : `Exported to ${opts.output}`;
}
