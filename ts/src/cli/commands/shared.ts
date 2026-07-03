/**
 * Helpers shared across command family modules (AC-853 split of command-handlers.ts).
 */
import { resolve, join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { asDbPath } from "../../domain/ids.js";

export function getMigrationsDir(): string {
  const thisDir = dirname(fileURLToPath(import.meta.url));
  return join(thisDir, "..", "..", "..", "migrations");
}

export function formatFatalCliError(err: unknown): string {
  if (err instanceof Error) {
    // Clean message only — no stack traces unless DEBUG is set
    if (process.env.DEBUG) {
      return err.stack ?? err.message;
    }
    return `Error: ${err.message}`;
  }
  return String(err);
}

export function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function parsePositiveInteger(raw: string | undefined, label: string): number {
  const parsed = Number.parseInt(raw ?? "", 10);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`${label} must be a positive integer`);
  }
  return parsed;
}

export async function getDbPath(): Promise<string> {
  const { loadSettings } = await import("../../config/index.js");
  const { mkdirSync } = await import("node:fs");
  const dbPath = resolve(loadSettings().dbPath);
  mkdirSync(dirname(dbPath), { recursive: true });
  return dbPath;
}

export async function loadProjectDefaults() {
  const { loadProjectConfig } = await import("../../config/index.js");
  return loadProjectConfig();
}

export interface SavedAgentTaskScenario {
  name: string;
  taskPrompt: string;
  rubric: string;
  referenceContext?: string;
  requiredConcepts?: string[];
  calibrationExamples?: Array<Record<string, unknown>>;
  revisionPrompt?: string;
  maxRounds?: number;
  qualityThreshold?: number;
}

export async function loadSavedAgentTaskScenario(
  name: string,
): Promise<SavedAgentTaskScenario | null> {
  const { loadSettings } = await import("../../config/index.js");
  const { resolveCustomJudgeScenario, renderAgentTaskPrompt } =
    await import("../../scenarios/custom-loader.js");

  const settings = loadSettings();
  const saved = resolveCustomJudgeScenario(resolve(settings.knowledgeRoot), name);
  if (!saved) {
    return null;
  }

  return {
    name: saved.name,
    taskPrompt: renderAgentTaskPrompt(saved.spec),
    rubric: saved.spec.judgeRubric,
    referenceContext: saved.spec.referenceContext ?? undefined,
    requiredConcepts: saved.spec.requiredConcepts ?? undefined,
    calibrationExamples: saved.spec.calibrationExamples ?? undefined,
    revisionPrompt: saved.spec.revisionPrompt ?? undefined,
    maxRounds: saved.spec.maxRounds,
    qualityThreshold: saved.spec.qualityThreshold,
  };
}

export async function resolveScenarioOption(explicit?: string): Promise<string | undefined> {
  if (explicit?.trim()) {
    return explicit.trim();
  }
  return (await loadProjectDefaults())?.defaultScenario;
}

export async function summarizeDirectory(
  root: string,
): Promise<{ exists: boolean; directories: number; files: number }> {
  const { existsSync, readdirSync } = await import("node:fs");
  if (!existsSync(root)) {
    return { exists: false, directories: 0, files: 0 };
  }

  let directories = 0;
  let files = 0;
  const stack = [root];

  while (stack.length > 0) {
    const current = stack.pop()!;
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        directories += 1;
        stack.push(join(current, entry.name));
      } else {
        files += 1;
      }
    }
  }

  return { exists: true, directories, files };
}

export async function buildProjectConfigSummary(): Promise<Record<string, unknown> | null> {
  const { findProjectConfigLocation, loadProjectConfig, loadSettings } =
    await import("../../config/index.js");
  const projectConfig = loadProjectConfig();
  if (!projectConfig) {
    return null;
  }

  const configLocation = findProjectConfigLocation();
  const settings = loadSettings();
  const dbPath = resolve(settings.dbPath);
  const knowledgeRoot = resolve(settings.knowledgeRoot);
  const { existsSync } = await import("node:fs");

  let totalRuns = 0;
  let activeRuns = 0;
  if (existsSync(dbPath)) {
    const { SQLiteStore } = await import("../../storage/index.js");
    const store = new SQLiteStore(asDbPath(dbPath));
    try {
      store.migrate(getMigrationsDir());
      const runs = store.listRuns(1000);
      totalRuns = runs.length;
      activeRuns = runs.filter((run) => run.status === "running").length;
    } finally {
      store.close();
    }
  }

  return {
    path: configLocation?.path ?? null,
    config_source: configLocation?.source ?? null,
    default_scenario: projectConfig.defaultScenario ?? null,
    provider: projectConfig.provider ?? null,
    model: projectConfig.model ?? null,
    gens: projectConfig.gens ?? null,
    runs_root: settings.runsRoot,
    knowledge_root: settings.knowledgeRoot,
    db_path: settings.dbPath,
    active_runs: activeRuns,
    total_runs: totalRuns,
    knowledge_state: await summarizeDirectory(knowledgeRoot),
  };
}

export async function getProvider(
  overrides: {
    providerType?: string;
    apiKey?: string;
    baseUrl?: string;
    model?: string;
  } = {},
) {
  const { createConfiguredProvider } = await import("../../providers/index.js");
  const { loadSettings } = await import("../../config/index.js");

  try {
    const { provider, config } = createConfiguredProvider(overrides, loadSettings());
    const model = config.model ?? provider.defaultModel();
    return { provider, model };
  } catch (err) {
    console.error(err instanceof Error ? err.message : String(err));
    process.exit(1);
  }
}
