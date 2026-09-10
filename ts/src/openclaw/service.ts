import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";

import { getConceptModel } from "../concepts/model.js";
import type { AppSettings } from "../config/index.js";
import { HarnessStore } from "../knowledge/harness-store.js";
import { getCapabilities, type Capabilities } from "../mcp/capabilities.js";
import type { SQLiteStore } from "../storage/index.js";
import { SCENARIO_REGISTRY } from "../scenarios/registry.js";
import { detectFamily } from "../scenarios/family-interfaces.js";
import type { ScenarioInterface } from "../scenarios/game-interface.js";
import {
  listSecureDirectoryNames,
  readSecureTextFile,
  writeSecureTextFile,
} from "../security/secure-local-files.js";
import { childProcessEnvWithoutControlPlaneSecrets } from "../security/child-process-env.js";
import {
  ensureSafeArtifactId,
  ensureSafeLegacyArtifactReadId,
  validateOpenClawArtifactPayload,
} from "./artifact-contract.js";
import {
  DistillJobError,
  DistillJobStore,
  isDistillJobStatus,
  type DistillJob,
  type DistillJobStatus,
} from "./distill-job-store.js";

const DISCOVERY_VERSION = "0.1.0";
const ARTIFACT_DIRECTORY = "_openclaw_artifacts";
const MAX_ARTIFACT_JSON_BYTES = 2 * 1024 * 1024;
const MAX_ARTIFACT_FILES = 10_000;
const MAX_DISTILL_SOURCE_ARTIFACTS = 1_000;
const MAX_DISTILL_JSON_BYTES = 512 * 1024;

export interface OpenClawServiceOpts {
  knowledgeRoot: string;
  settings: AppSettings;
  openStore: () => SQLiteStore;
}

export interface ArtifactSummary {
  id: string;
  name: string;
  artifact_type: string;
  scenario: string;
  version: number;
}

export interface ScenarioCapabilities {
  scenario_name: string;
  evaluation_mode: string;
  has_harness: boolean;
  has_policy: boolean;
  has_playbook: boolean;
  harness_count: number;
  best_score: number | null;
  best_elo: number | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function readRequiredString(body: Record<string, unknown>, key: string): string {
  const value = body[key];
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${key} is required`);
  }
  return value.trim();
}

function readOptionalString(body: Record<string, unknown>, key: string): string | undefined {
  const value = body[key];
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function readInteger(body: Record<string, unknown>, key: string, fallback: number, min: number, max: number): number {
  const value = body[key];
  if (typeof value !== "number" || !Number.isInteger(value)) {
    return fallback;
  }
  return Math.min(max, Math.max(min, value));
}

function readRecord(body: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = body[key];
  if (!isRecord(value)) {
    throw new Error(`${key} is required`);
  }
  return value;
}

function readStringList(body: Record<string, unknown>, key: string): string[] {
  const value = body[key];
  if (value === undefined) {
    return [];
  }
  if (!Array.isArray(value) || !value.every((entry) => typeof entry === "string")) {
    throw new Error(`${key} must be a list of strings`);
  }
  if (value.length > MAX_DISTILL_SOURCE_ARTIFACTS) {
    throw new Error(`${key} exceeds ${MAX_DISTILL_SOURCE_ARTIFACTS} item limit`);
  }
  return value.map((entry) => ensureSafeArtifactId(entry.trim()));
}

function toHarnessModuleName(artifactId: string): string {
  const normalized = artifactId.replace(/[^A-Za-z0-9_]/g, "_");
  const direct = `openclaw_${normalized}`;
  if (direct.length <= 128) return direct;
  const digest = createHash("sha256").update(artifactId).digest("hex").slice(0, 16);
  return `openclaw_${normalized.slice(0, 96)}_${digest}`;
}

function humanizeScenarioName(name: string): string {
  return name
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function readArtifactRecord(knowledgeRoot: string, filename: string): Record<string, unknown> | null {
  const raw = readSecureTextFile(
    knowledgeRoot,
    [ARTIFACT_DIRECTORY],
    filename,
    MAX_ARTIFACT_JSON_BYTES,
  );
  if (raw === null) return null;
  try {
    const parsed = JSON.parse(raw);
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function listArtifactRecords(knowledgeRoot: string): Record<string, unknown>[] {
  return listSecureDirectoryNames(
    knowledgeRoot,
    [ARTIFACT_DIRECTORY],
    MAX_ARTIFACT_FILES,
  )
    .filter((name) => name.endsWith(".json"))
    .sort()
    .map((name) => readArtifactRecord(knowledgeRoot, name))
    .filter((record): record is Record<string, unknown> => record !== null);
}

function buildArtifactSummary(data: Record<string, unknown>, fallbackId: string): ArtifactSummary {
  return {
    id: typeof data.id === "string" ? data.id : fallbackId,
    name: typeof data.name === "string" ? data.name : "",
    artifact_type: typeof data.artifact_type === "string" ? data.artifact_type : "",
    scenario: typeof data.scenario === "string" ? data.scenario : "",
    version: typeof data.version === "number" ? data.version : 0,
  };
}

const DISTILL_COMMAND_PLACEHOLDERS = new Set(["{job_id}", "{scenario}"]);

export function parseDistillSidecarCommand(rawCommand: string): string[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawCommand);
  } catch {
    throw new DistillJobError(
      "AUTOCONTEXT_OPENCLAW_DISTILL_SIDECAR_COMMAND must be a JSON argv array; " +
      "shell command strings are not allowed",
    );
  }
  return validateDistillSidecarArgv(parsed);
}

export function expandDistillSidecarCommand(
  commandArgv: readonly string[],
  job: Pick<DistillJob, "job_id" | "scenario">,
): string[] {
  const validated = validateDistillSidecarArgv(commandArgv);
  validateDistillJobValue(job.job_id, "job ID");
  validateDistillJobValue(job.scenario, "scenario");
  const replacements = new Map<string, readonly [string, string]>([
    ["{job_id}", [job.job_id, "job ID"]],
    ["{scenario}", [job.scenario, "scenario"]],
  ]);
  return validated.map((argument) => {
    const replacement = replacements.get(argument);
    if (!replacement) return argument;
    const [value, label] = replacement;
    if (value.trimStart().startsWith("-")) {
      throw new DistillJobError(`distill ${label} placeholder value must not be option-shaped`);
    }
    return value;
  });
}

function validateDistillSidecarArgv(value: unknown): string[] {
  if (!Array.isArray(value) || value.length === 0) {
    throw new DistillJobError("distill sidecar command must be a non-empty JSON argv array");
  }
  const validated = value.map((argument, index) => {
    if (typeof argument !== "string" || !argument || argument.includes("\0")) {
      throw new DistillJobError(
        "distill sidecar argv entries must be non-empty strings without NUL bytes",
      );
    }
    if (
      (argument.includes("{") || argument.includes("}"))
      && !DISTILL_COMMAND_PLACEHOLDERS.has(argument)
    ) {
      throw new DistillJobError(
        "distill sidecar placeholders must be whole argv entries and may only be {job_id} or {scenario}",
      );
    }
    if (index === 0 && DISTILL_COMMAND_PLACEHOLDERS.has(argument)) {
      throw new DistillJobError("distill sidecar executable must be a fixed literal argv entry");
    }
    return argument;
  });
  return validated;
}

function validateDistillJobValue(value: string, label: string): void {
  if (!value || value.includes("\0")) {
    throw new DistillJobError(`distill ${label} must be non-empty and must not contain NUL bytes`);
  }
}

export class OpenClawService {
  readonly #knowledgeRoot: string;
  readonly #settings: AppSettings;
  readonly #openStore: () => SQLiteStore;
  readonly #distillJobs: DistillJobStore;

  constructor(opts: OpenClawServiceOpts) {
    this.#knowledgeRoot = opts.knowledgeRoot;
    this.#settings = opts.settings;
    this.#openStore = opts.openStore;
    this.#distillJobs = new DistillJobStore(opts.knowledgeRoot);
  }

  evaluateStrategy(body: Record<string, unknown>): Record<string, unknown> {
    const scenarioName = readRequiredString(body, "scenario_name");
    const strategy = readRecord(body, "strategy");
    const numMatches = readInteger(body, "num_matches", 3, 1, 100);
    const seedBase = readInteger(body, "seed_base", 42, Number.MIN_SAFE_INTEGER, Number.MAX_SAFE_INTEGER);
    const scenario = this.#loadGameScenario(scenarioName);
    const scores: number[] = [];
    for (let i = 0; i < numMatches; i += 1) {
      const result = scenario.executeMatch(strategy, seedBase + i);
      scores.push(result.score);
    }
    return {
      scenario: scenarioName,
      matches: numMatches,
      scores,
      mean_score: scores.length > 0 ? scores.reduce((sum, score) => sum + score, 0) / scores.length : 0,
      best_score: scores.length > 0 ? Math.max(...scores) : 0,
    };
  }

  validateStrategy(body: Record<string, unknown>): Record<string, unknown> {
    const scenarioName = readRequiredString(body, "scenario_name");
    const strategy = readRecord(body, "strategy");
    const scenario = this.#loadGameScenario(scenarioName);
    const state = scenario.initialState(42);
    const [valid, reason] = scenario.validateActions(state, "challenger", strategy);
    const harnessLoaded = this.#listHarnessModules(scenarioName);
    return {
      valid,
      reason,
      scenario: scenarioName,
      harness_loaded: harnessLoaded,
      harness_passed: valid,
      harness_errors: valid ? [] : [reason],
    };
  }

  publishArtifact(body: Record<string, unknown>): Record<string, unknown> {
    const { artifactId, artifactType, scenario, data } = validateOpenClawArtifactPayload(body);
    const serialized = `${JSON.stringify(data, null, 2)}\n`;
    if (Buffer.byteLength(serialized, "utf-8") > MAX_ARTIFACT_JSON_BYTES) {
      throw new Error(`artifact JSON exceeds ${MAX_ARTIFACT_JSON_BYTES} byte limit`);
    }
    let harnessStore: HarnessStore | undefined;
    if (artifactType === "harness" && typeof data.source_code === "string" && data.source_code.trim()) {
      harnessStore = new HarnessStore(this.#knowledgeRoot, scenario);
      // Resolve existing storage before publishing metadata so a pre-seeded
      // scenario/harness symlink cannot leave a misleading published record.
      harnessStore.listHarness();
    }
    const path = writeSecureTextFile(
      this.#knowledgeRoot,
      [ARTIFACT_DIRECTORY],
      `${artifactId}.json`,
      serialized,
      { maxBytes: MAX_ARTIFACT_JSON_BYTES, replace: true },
    );
    if (harnessStore && typeof data.source_code === "string") {
      harnessStore.writeVersioned(toHarnessModuleName(artifactId), data.source_code, 0);
    }

    return {
      status: "published",
      artifact_id: artifactId,
      artifact_type: artifactType,
      path,
    };
  }

  listArtifacts(params: URLSearchParams): ArtifactSummary[] {
    const scenario = params.get("scenario") ?? undefined;
    const artifactType = params.get("artifact_type") ?? undefined;
    return listArtifactRecords(this.#knowledgeRoot)
      .filter((data) => scenario === undefined || data.scenario === scenario)
      .filter((data) => artifactType === undefined || data.artifact_type === artifactType)
      .map((data) => buildArtifactSummary(data, typeof data.id === "string" ? data.id : ""));
  }

  fetchArtifact(artifactId: string): Record<string, unknown> | null {
    return readArtifactRecord(
      this.#knowledgeRoot,
      `${ensureSafeLegacyArtifactReadId(artifactId)}.json`,
    );
  }

  distillStatus(params: URLSearchParams): Record<string, unknown> {
    const scenario = params.get("scenario") ?? undefined;
    const jobs = this.#distillJobs.listJobs(scenario);
    return {
      active_jobs: jobs.filter((job) => job.status === "pending" || job.status === "running").length,
      jobs,
    };
  }

  triggerDistillation(body: Record<string, unknown>): Record<string, unknown> {
    const scenario = readRequiredString(body, "scenario");
    const sourceArtifactIds = readStringList(body, "source_artifact_ids");
    const trainingConfig = body.training_config === undefined
      ? {}
      : readRecord(body, "training_config");
    const trainingConfigJson = JSON.stringify(trainingConfig);
    if (Buffer.byteLength(trainingConfigJson, "utf-8") > MAX_DISTILL_JSON_BYTES) {
      throw new DistillJobError(
        `training_config exceeds ${MAX_DISTILL_JSON_BYTES} byte limit`,
      );
    }
    const job = this.#distillJobs.createJob({ scenario, sourceArtifactIds, trainingConfig });
    const commandTemplate = this.#settings.openclawDistillSidecarCommand.trim();
    if (!commandTemplate) {
      const errorMessage = (
        "No distillation sidecar configured. Set " +
        "AUTOCONTEXT_OPENCLAW_DISTILL_SIDECAR_COMMAND."
      );
      const failed = this.#distillJobs.transition(job.job_id, "failed", { errorMessage });
      return {
        error: errorMessage,
        job_id: failed?.job_id ?? job.job_id,
        status: failed?.status ?? "failed",
        scenario: failed?.scenario ?? job.scenario,
      };
    }

    let command: string[];
    try {
      command = expandDistillSidecarCommand(
        parseDistillSidecarCommand(commandTemplate),
        job,
      );
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      const failed = this.#distillJobs.transition(job.job_id, "failed", { errorMessage });
      return {
        error: errorMessage,
        job_id: failed?.job_id ?? job.job_id,
        status: failed?.status ?? "failed",
        scenario: failed?.scenario ?? job.scenario,
      };
    }

    const [bin, ...args] = command;
    try {
      const child = spawn(bin!, args, {
        cwd: dirname(this.#knowledgeRoot),
        detached: true,
        shell: false,
        stdio: "ignore",
        env: {
          ...childProcessEnvWithoutControlPlaneSecrets(),
          AUTOCONTEXT_DISTILL_JOB_ID: job.job_id,
          AUTOCONTEXT_DISTILL_SCENARIO: job.scenario,
          AUTOCONTEXT_DISTILL_TRAINING_CONFIG: JSON.stringify(job.training_config),
        },
      });
      child.once("spawn", () => {
        try {
          const current = this.#distillJobs.getJob(job.job_id);
          if (current?.status === "pending") {
            this.#distillJobs.transition(job.job_id, "running");
          }
          child.unref();
        } catch (error) {
          this.#failDistillJobAfterSpawn(job.job_id, error);
          try {
            child.kill("SIGTERM");
          } catch {
            // The process may already have exited while persistence failed.
          }
          child.unref();
        }
      });
      child.once("error", (error) => {
        this.#failDistillJobAfterSpawn(job.job_id, error);
      });
      // Node reports ENOENT/EACCES asynchronously. Keep the durable state
      // pending until ChildProcess emits the successful `spawn` event.
      return { ...job };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const failed = this.#distillJobs.transition(job.job_id, "failed", { errorMessage: message });
      return {
        error: message,
        job_id: failed?.job_id ?? job.job_id,
        status: failed?.status ?? "failed",
        scenario: failed?.scenario ?? job.scenario,
      };
    }
  }

  getDistillJob(jobId: string): DistillJob | null {
    return this.#distillJobs.getJob(jobId);
  }

  updateDistillJob(jobId: string, body: Record<string, unknown>): DistillJob | null {
    const status = readRequiredString(body, "status");
    if (!isDistillJobStatus(status)) {
      throw new DistillJobError(`Invalid distill job status: ${status}`);
    }
    const trainingMetrics = body.training_metrics === undefined || body.training_metrics === null
      ? null
      : readRecord(body, "training_metrics");
    return this.#distillJobs.transition(jobId, status, {
      resultArtifactId: readOptionalString(body, "result_artifact_id"),
      errorMessage: readOptionalString(body, "error_message"),
      trainingMetrics,
    });
  }

  capabilities(): Capabilities {
    return getCapabilities();
  }

  advertiseCapabilities(): Record<string, unknown> {
    const scenarioCapabilities: Record<string, ScenarioCapabilities> = {};
    for (const scenarioName of Object.keys(SCENARIO_REGISTRY).sort()) {
      try {
        scenarioCapabilities[scenarioName] = this.discoverScenarioCapabilities(scenarioName);
      } catch {
        // Keep capability discovery resilient, matching Python's best-effort behavior.
      }
    }
    return {
      version: DISCOVERY_VERSION,
      runtime_health: this.runtimeHealth(),
      concept_model: getConceptModel(),
      scenario_capabilities: scenarioCapabilities,
      artifact_counts: this.#artifactCounts(),
    };
  }

  discoverScenarioCapabilities(scenarioName: string): ScenarioCapabilities {
    const ScenarioClass = SCENARIO_REGISTRY[scenarioName];
    if (!ScenarioClass) {
      throw new Error(`Scenario '${scenarioName}' not found`);
    }
    const scenario = new ScenarioClass();
    const family = detectFamily(scenario);
    if (family === null) {
      throw new Error(`Unable to determine scenario family for '${scenarioName}'`);
    }
    const harness = new HarnessStore(this.#knowledgeRoot, scenarioName);
    const harnessModules = harness.listHarness();
    const best = this.#getBestGenerationMetrics(scenarioName);
    return {
      scenario_name: scenarioName,
      evaluation_mode: family === "agent_task" ? "judge" : family === "game" ? "tournament" : family,
      has_harness: harnessModules.length > 0,
      has_policy: this.#hasPolicyArtifact(scenarioName),
      has_playbook: this.#hasPlaybook(scenarioName),
      harness_count: harnessModules.length,
      best_score: best?.best_score ?? null,
      best_elo: best?.elo ?? null,
    };
  }

  runtimeHealth(): Record<string, unknown> {
    return {
      executor_mode: this.#settings.executorMode,
      agent_provider: this.#settings.agentProvider,
      harness_mode: this.#settings.harnessMode,
      rlm_enabled: this.#settings.rlmEnabled,
      available_models: {
        competitor: this.#settings.modelCompetitor,
        analyst: this.#settings.modelAnalyst,
        coach: this.#settings.modelCoach,
        architect: this.#settings.modelArchitect,
        judge: this.#settings.judgeModel,
      },
      openclaw_runtime_kind: this.#settings.openclawRuntimeKind.trim() || null,
      openclaw_compatibility_version: this.#settings.openclawCompatibilityVersion.trim() || null,
    };
  }

  scenarioArtifactLookup(scenarioName: string): Array<Record<string, unknown>> {
    return listArtifactRecords(this.#knowledgeRoot)
      .filter((data) => data.scenario === scenarioName)
      .map((data) => ({
        artifact_id: typeof data.id === "string" ? data.id : "",
        name: typeof data.name === "string" ? data.name : "",
        artifact_type: typeof data.artifact_type === "string" ? data.artifact_type : "",
        scenario: typeof data.scenario === "string" ? data.scenario : "",
        version: typeof data.version === "number" ? data.version : 0,
      }));
  }

  skillManifest(): Record<string, unknown> {
    return {
      name: "autocontext",
      version: getCapabilities().version,
      description: "autocontext iterative strategy evolution and evaluation system",
      capabilities: [
        "scenario_evaluation",
        "strategy_validation",
        "artifact_management",
        "knowledge_export",
        "strategy_search",
      ],
      scenarios: Object.keys(SCENARIO_REGISTRY).sort().map((name) => this.#scenarioInfo(name)),
      mcp_tools: [
        "autocontext_capabilities",
        "autocontext_list_scenarios",
        "autocontext_describe_scenario",
        "autocontext_run_match",
        "autocontext_run_tournament",
        "autocontext_read_playbook",
        "autocontext_list_solved",
        "autocontext_search_strategies",
        "autocontext_solve_scenario",
        "autocontext_solve_status",
        "autocontext_solve_result",
      ],
      rest_base_path: "/api/openclaw",
    };
  }

  #loadGameScenario(scenarioName: string): ScenarioInterface {
    const ScenarioClass = SCENARIO_REGISTRY[scenarioName];
    if (!ScenarioClass) {
      const supported = Object.keys(SCENARIO_REGISTRY).sort().join(", ");
      throw new Error(`Unknown scenario '${scenarioName}'. Available: ${supported}`);
    }
    return new ScenarioClass();
  }

  #listHarnessModules(scenarioName: string): string[] {
    try {
      return new HarnessStore(this.#knowledgeRoot, scenarioName).listHarness();
    } catch {
      return [];
    }
  }

  #hasPlaybook(scenarioName: string): boolean {
    const path = join(this.#knowledgeRoot, scenarioName, "playbook.md");
    if (!existsSync(path)) return false;
    return readFileSync(path, "utf-8").trim().length > 0;
  }

  #hasPolicyArtifact(scenarioName: string): boolean {
    return listArtifactRecords(this.#knowledgeRoot)
      .some((artifact) => artifact.artifact_type === "policy" && artifact.scenario === scenarioName);
  }

  #artifactCounts(): Record<string, number> {
    const counts: Record<string, number> = {};
    for (const artifact of listArtifactRecords(this.#knowledgeRoot)) {
      if (typeof artifact.artifact_type !== "string" || !artifact.artifact_type) continue;
      counts[artifact.artifact_type] = (counts[artifact.artifact_type] ?? 0) + 1;
    }
    return counts;
  }

  #getBestGenerationMetrics(scenarioName: string): { best_score: number | null; elo: number | null } | null {
    return this.#withStore((store) => {
      const completedBest = store.getBestGenerationForScenario(scenarioName);
      if (completedBest) {
        return { best_score: completedBest.best_score, elo: completedBest.elo };
      }
      let fallback: { best_score: number | null; elo: number | null } | null = null;
      for (const run of store.listRuns(200, scenarioName)) {
        for (const generation of store.getGenerations(run.run_id)) {
          if (generation.status !== "completed") {
            continue;
          }
          if (
            fallback === null
            || (generation.best_score ?? Number.NEGATIVE_INFINITY) > (fallback.best_score ?? Number.NEGATIVE_INFINITY)
            || (
              generation.best_score === fallback.best_score
              && (generation.elo ?? Number.NEGATIVE_INFINITY) > (fallback.elo ?? Number.NEGATIVE_INFINITY)
            )
          ) {
            fallback = { best_score: generation.best_score, elo: generation.elo };
          }
        }
      }
      return fallback;
    });
  }

  #scenarioInfo(name: string): Record<string, unknown> {
    const ScenarioClass = SCENARIO_REGISTRY[name]!;
    const scenario = new ScenarioClass();
    const family = detectFamily(scenario);
    return {
      name,
      display_name: humanizeScenarioName(name),
      scenario_type: family === "game" ? "parametric" : family ?? "unknown",
      description: scenario.describeRules().slice(0, 500),
      strategy_interface: scenario.describeStrategyInterface(),
    };
  }

  #withStore<T>(fn: (store: SQLiteStore) => T): T {
    const store = this.#openStore();
    try {
      return fn(store);
    } finally {
      store.close();
    }
  }

  #failDistillJobAfterSpawn(jobId: string, error: unknown): void {
    const message = error instanceof Error ? error.message : String(error);
    try {
      const current = this.#distillJobs.getJob(jobId);
      if (current?.status === "pending" || current?.status === "running") {
        this.#distillJobs.transition(jobId, "failed", { errorMessage: message });
      }
    } catch {
      // EventEmitter error handlers must never throw: doing so would turn a
      // sidecar launch failure into an unhandled process-level exception.
    }
  }
}
