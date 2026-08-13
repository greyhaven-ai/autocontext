import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, relative } from "node:path";
import {
  buildPiPackage,
  defaultPiPackageOutputDir,
  writePiPackage,
  type PiPackage,
  type WrittenPiPackage,
} from "../knowledge/pi-package.js";

export type ExportFormat = "json" | "pi-package";

export class ExportUsageError extends Error {}

export function normalizeExportFormat(value: string | undefined): ExportFormat {
  const normalized = (value ?? "json").trim().toLowerCase();
  if (normalized === "strategy") return "json";
  if (normalized === "json" || normalized === "pi-package") return normalized;
  throw new ExportUsageError(
    "Error: --format must be one of json, pi-package (strategy is accepted as an alias for json)",
  );
}

export const EXPORT_HELP_TEXT = `autoctx export — Export strategy package for a run or scenario

Usage:
  autoctx export <run-id> [--format json] [--output <file>] [--json]
  autoctx export --scenario <name> --format pi-package [--output <directory>] [--json]

Options:
  <run-id>             Run to export as a strategy package
  --run-id <id>        Same run id as a named option
  -s, --scenario <name>
                       Scenario to export
  --format <format>    Artifact format: json or pi-package (default: json)
                       Legacy value "strategy" is an alias for json
  -o, --output <path>  JSON file or Pi-package directory
                       JSON defaults to stdout; Pi defaults to <scenario>-pi-package
  --json               Emit a structured receipt when writing an artifact

See also: import-package, run, replay`;

export interface ExportCommandValues {
  scenario?: string;
  "run-id"?: string;
  positionals?: string[];
  output?: string;
  format?: string;
  json?: boolean;
}

export interface ExportCommandPlan {
  scenarioName: string;
  runId?: string;
  output?: string;
  format: ExportFormat;
  json: boolean;
}

export async function planExportCommand(
  values: ExportCommandValues,
  resolveScenarioOption: (scenario: string | undefined) => Promise<string | undefined>,
  resolveRunScenario: (runId: string) => Promise<string | undefined>,
): Promise<ExportCommandPlan> {
  const format = normalizeExportFormat(values.format);
  const explicitScenario = values.scenario?.trim();
  if (explicitScenario) {
    const scenarioName = await resolveScenarioOption(explicitScenario);
    if (!scenarioName) {
      throw new ExportUsageError("Error: --scenario or <run-id> is required");
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
        format,
        json: !!values.json,
      };
    }
    return {
      scenarioName,
      runId: undefined,
      output: values.output,
      format,
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
      format,
      json: !!values.json,
    };
  }

  throw new ExportUsageError("Error: --scenario or <run-id> is required");
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
  format?: ExportFormat;
  json?: boolean;
  packageVersion?: string;
  exportStrategyPackage: (args: {
    scenarioName: string;
    sourceRunId?: string;
    artifacts: TArtifacts;
    store: TStore;
  }) => TResult;
  artifacts: TArtifacts;
  store: TStore;
  writeOutputFile?: (path: string, content: string) => void;
  writePiPackageOutput?: (pkg: PiPackage, outputDir: string) => WrittenPiPackage;
}): string {
  const result = opts.exportStrategyPackage({
    scenarioName: opts.scenarioName,
    ...(opts.runId ? { sourceRunId: opts.runId } : {}),
    artifacts: opts.artifacts,
    store: opts.store,
  });
  const format = opts.format ?? "json";
  if (format === "pi-package") {
    const outputDir = opts.output ?? defaultPiPackageOutputDir(opts.scenarioName);
    const writePiPackageOutput = opts.writePiPackageOutput ?? writePiPackage;
    const written = writePiPackageOutput(
      buildPiPackage(result, opts.packageVersion ?? "0.0.0"),
      outputDir,
    );
    if (opts.json) {
      return JSON.stringify({
        scenario: opts.scenarioName,
        format,
        output_path: outputDir,
        file_count: written.files.length,
        files: written.files.map((path) => relative(outputDir, path)),
      });
    }
    return `Exported ${opts.scenarioName} Pi package to ${outputDir}`;
  }

  const serialized = `${JSON.stringify(result, null, 2)}\n`;

  if (!opts.output) {
    return serialized.trimEnd();
  }

  const writeOutputFile = opts.writeOutputFile ?? writeOutputFileWithParents;
  writeOutputFile(opts.output, serialized);
  if (opts.json) {
    const lessons = Array.isArray(result.lessons) ? result.lessons : [];
    const harness = result.harness && typeof result.harness === "object" && !Array.isArray(result.harness)
      ? result.harness as Record<string, unknown>
      : {};
    return JSON.stringify({
      scenario: opts.scenarioName,
      format: "json",
      output_path: opts.output,
      best_score: typeof result.best_score === "number" ? result.best_score : 0,
      lessons_count: lessons.length,
      harness_count: Object.keys(harness).length,
    });
  }
  return `Exported ${opts.scenarioName} package to ${opts.output}`;
}
