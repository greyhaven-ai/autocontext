/**
 * `export`, `export-training-data`, `import-package` commands
 * (AC-853 split of command-handlers.ts).
 */
import { parseArgs } from "node:util";
import { resolve, dirname } from "node:path";
import { asDbPath } from "../../domain/ids.js";
import { errorMessage, getMigrationsDir, resolveScenarioOption } from "./shared.js";

export async function cmdExport(dbPath: string): Promise<void> {
  const { values, positionals } = parseArgs({
    args: process.argv.slice(3),
    allowPositionals: true,
    options: {
      scenario: { type: "string", short: "s" },
      "run-id": { type: "string" },
      output: { type: "string", short: "o" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const { executeExportCommandWorkflow, EXPORT_HELP_TEXT, planExportCommand } =
    await import("../export-command-workflow.js");

  if (values.help) {
    console.log(EXPORT_HELP_TEXT);
    process.exit(0);
  }

  const { loadSettings } = await import("../../config/index.js");
  const { ArtifactStore } = await import("../../knowledge/artifact-store.js");
  const { exportStrategyPackage } = await import("../../knowledge/package.js");
  const { SQLiteStore } = await import("../../storage/index.js");

  const settings = loadSettings();
  const store = new SQLiteStore(asDbPath(dbPath));
  store.migrate(getMigrationsDir());

  let plan;
  try {
    plan = await planExportCommand(
      { ...values, positionals },
      resolveScenarioOption,
      async (runId) => store.getRun(runId)?.scenario,
    );
  } catch (error) {
    console.error(errorMessage(error));
    store.close();
    process.exit(1);
  }

  const artifacts = new ArtifactStore({
    runsRoot: resolve(settings.runsRoot),
    knowledgeRoot: resolve(settings.knowledgeRoot),
  });
  try {
    const { writeFileSync, mkdirSync } = await import("node:fs");
    console.log(
      executeExportCommandWorkflow({
        scenarioName: plan.scenarioName,
        runId: plan.runId,
        output: plan.output,
        json: plan.json,
        exportStrategyPackage,
        artifacts,
        store,
        writeOutputFile: (path, content) => {
          mkdirSync(dirname(path), { recursive: true });
          writeFileSync(path, content, "utf-8");
        },
      }),
    );
  } finally {
    store.close();
  }
}

export async function cmdExportTrainingData(dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      "run-id": { type: "string" },
      scenario: { type: "string" },
      "all-runs": { type: "boolean" },
      output: { type: "string", short: "o" },
      "include-matches": { type: "boolean" },
      "kept-only": { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const {
    executeExportTrainingDataCommandWorkflow,
    EXPORT_TRAINING_DATA_HELP_TEXT,
    planExportTrainingDataCommand,
  } = await import("../export-training-data-command-workflow.js");

  if (values.help) {
    console.log(EXPORT_TRAINING_DATA_HELP_TEXT);
    process.exit(0);
  }

  let plan;
  try {
    plan = planExportTrainingDataCommand(values);
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  const { SQLiteStore } = await import("../../storage/index.js");
  const { loadSettings } = await import("../../config/index.js");
  const { ArtifactStore } = await import("../../knowledge/artifact-store.js");
  const { exportTrainingData } = await import("../../training/export.js");

  const settings = loadSettings();
  const store = new SQLiteStore(asDbPath(dbPath));
  store.migrate(getMigrationsDir());
  const artifacts = new ArtifactStore({
    runsRoot: resolve(settings.runsRoot),
    knowledgeRoot: resolve(settings.knowledgeRoot),
  });

  try {
    const { writeFileSync, mkdirSync } = await import("node:fs");
    const result = executeExportTrainingDataCommandWorkflow({
      plan,
      store,
      artifacts,
      exportTrainingData,
      writeOutputFile: (path, content) => {
        mkdirSync(dirname(path), { recursive: true });
        writeFileSync(path, content, "utf-8");
      },
    });
    for (const line of result.stderrLines) {
      console.error(line);
    }
    console.log(result.stdout);
  } finally {
    store.close();
  }
}

export async function cmdImportPackage(_dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      file: { type: "string", short: "f" },
      scenario: { type: "string", short: "s" },
      conflict: { type: "string", default: "overwrite" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const {
    executeImportPackageCommandWorkflow,
    IMPORT_PACKAGE_HELP_TEXT,
    planImportPackageCommand,
  } = await import("../import-package-command-workflow.js");

  if (values.help) {
    console.log(IMPORT_PACKAGE_HELP_TEXT);
    process.exit(0);
  }

  let plan;
  try {
    plan = planImportPackageCommand(values);
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  const { readFileSync } = await import("node:fs");
  const { loadSettings } = await import("../../config/index.js");
  const { ArtifactStore } = await import("../../knowledge/artifact-store.js");
  const { importStrategyPackage } = await import("../../knowledge/package.js");

  const settings = loadSettings();
  const raw = readFileSync(plan.file, "utf-8");
  const artifacts = new ArtifactStore({
    runsRoot: resolve(settings.runsRoot),
    knowledgeRoot: resolve(settings.knowledgeRoot),
  });
  console.log(
    executeImportPackageCommandWorkflow({
      rawPackage: raw,
      artifacts,
      skillsRoot: resolve(settings.skillsRoot),
      scenarioOverride: plan.scenarioOverride,
      conflictPolicy: plan.conflictPolicy,
      importStrategyPackage,
    }),
  );
}
