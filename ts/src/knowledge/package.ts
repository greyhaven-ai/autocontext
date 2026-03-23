import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import type { SQLiteStore } from "../storage/index.js";
import { SCENARIO_REGISTRY } from "../scenarios/registry.js";
import { ArtifactStore, EMPTY_PLAYBOOK_SENTINEL } from "./artifact-store.js";
import { HarnessStore } from "./harness-store.js";
import { PLAYBOOK_MARKERS } from "./playbook.js";
import { SkillPackage, cleanLessons, type SkillPackageData } from "./skill-package.js";

const PACKAGE_FORMAT_VERSION = 1;

export type ConflictPolicy = "overwrite" | "merge" | "skip";

export interface StrategyPackageData extends SkillPackageData {
  formatVersion?: number;
}

export interface ImportStrategyPackageResult {
  scenario: string;
  playbookWritten: boolean;
  harnessWritten: string[];
  harnessSkipped: string[];
  skillWritten: boolean;
  metadataWritten: boolean;
  conflictPolicy: ConflictPolicy;
}

interface PersistedPackageMetadata {
  format_version?: number;
  best_strategy?: Record<string, unknown> | null;
  best_score?: number;
  best_elo?: number;
  metadata?: Record<string, unknown>;
}

function displayNameForScenario(scenarioName: string): string {
  return scenarioName.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function descriptionForScenario(scenarioName: string): string {
  const ScenarioClass = SCENARIO_REGISTRY[scenarioName];
  if (!ScenarioClass) {
    return `Exported knowledge for ${scenarioName}`;
  }
  const scenario = new ScenarioClass();
  return scenario.describeRules();
}

function packageMetadataPath(knowledgeRoot: string, scenarioName: string): string {
  return join(knowledgeRoot, scenarioName, "package_metadata.json");
}

function readPackageMetadata(knowledgeRoot: string, scenarioName: string): PersistedPackageMetadata {
  const path = packageMetadataPath(knowledgeRoot, scenarioName);
  if (!existsSync(path)) return {};
  try {
    const parsed = JSON.parse(readFileSync(path, "utf-8")) as PersistedPackageMetadata;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function writePackageMetadata(
  knowledgeRoot: string,
  scenarioName: string,
  payload: PersistedPackageMetadata,
): void {
  const path = packageMetadataPath(knowledgeRoot, scenarioName);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(payload, null, 2) + "\n", "utf-8");
}

function extractMarkedSection(content: string, startMarker: string, endMarker: string): string {
  const start = content.indexOf(startMarker);
  const end = content.indexOf(endMarker);
  if (start === -1 || end === -1 || end <= start) return "";
  return content.slice(start + startMarker.length, end).trim();
}

function lessonsFromPlaybook(playbook: string): string[] {
  const lessonsBlock = extractMarkedSection(
    playbook,
    PLAYBOOK_MARKERS.LESSONS_START,
    PLAYBOOK_MARKERS.LESSONS_END,
  );
  if (!lessonsBlock) return [];
  const rawBullets = lessonsBlock
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.startsWith("-"));
  return cleanLessons(rawBullets);
}

function hintsFromPlaybook(playbook: string): string {
  return extractMarkedSection(
    playbook,
    PLAYBOOK_MARKERS.HINTS_START,
    PLAYBOOK_MARKERS.HINTS_END,
  );
}

function harnessForScenario(knowledgeRoot: string, scenarioName: string): Record<string, string> {
  const store = new HarnessStore(knowledgeRoot, scenarioName);
  const harness: Record<string, string> = {};
  for (const name of store.listHarness()) {
    const source = store.read(name);
    if (source) {
      harness[name] = source;
    }
  }
  return harness;
}

function bestStrategyForScenario(
  store: SQLiteStore,
  scenarioName: string,
  persisted: PersistedPackageMetadata,
): Record<string, unknown> | null {
  const bestMatch = store.getBestMatchForScenario(scenarioName);
  if (bestMatch?.strategy_json) {
    try {
      return JSON.parse(bestMatch.strategy_json) as Record<string, unknown>;
    } catch {
      // fall through to persisted metadata
    }
  }
  return persisted.best_strategy ?? null;
}

function coerceHarness(raw: unknown): Record<string, string> {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const harness: Record<string, string> = {};
  for (const [key, value] of Object.entries(raw)) {
    if (typeof value === "string") {
      harness[key] = value;
    }
  }
  return harness;
}

function coercePackage(raw: Record<string, unknown>, scenarioOverride?: string): StrategyPackageData {
  const scenarioName = scenarioOverride
    ?? (typeof raw.scenario_name === "string" ? raw.scenario_name : undefined)
    ?? (typeof raw.scenarioName === "string" ? raw.scenarioName : undefined)
    ?? "unknown";

  const data: StrategyPackageData = {
    formatVersion:
      typeof raw.format_version === "number"
        ? raw.format_version
        : typeof raw.formatVersion === "number"
          ? raw.formatVersion
          : PACKAGE_FORMAT_VERSION,
    scenarioName,
    displayName:
      typeof raw.display_name === "string"
        ? raw.display_name
        : typeof raw.displayName === "string"
          ? raw.displayName
          : displayNameForScenario(scenarioName),
    description: typeof raw.description === "string" ? raw.description : `Exported knowledge for ${scenarioName}`,
    playbook: typeof raw.playbook === "string" ? raw.playbook : "",
    lessons: Array.isArray(raw.lessons) ? raw.lessons.filter((v): v is string => typeof v === "string") : [],
    bestStrategy:
      raw.best_strategy && typeof raw.best_strategy === "object" && !Array.isArray(raw.best_strategy)
        ? (raw.best_strategy as Record<string, unknown>)
        : raw.bestStrategy && typeof raw.bestStrategy === "object" && !Array.isArray(raw.bestStrategy)
          ? (raw.bestStrategy as Record<string, unknown>)
          : null,
    bestScore:
      typeof raw.best_score === "number"
        ? raw.best_score
        : typeof raw.bestScore === "number"
          ? raw.bestScore
          : 0,
    bestElo:
      typeof raw.best_elo === "number"
        ? raw.best_elo
        : typeof raw.bestElo === "number"
          ? raw.bestElo
          : 1500,
    hints: typeof raw.hints === "string" ? raw.hints : "",
    harness: coerceHarness(raw.harness),
    metadata:
      raw.metadata && typeof raw.metadata === "object" && !Array.isArray(raw.metadata)
        ? (raw.metadata as Record<string, unknown>)
        : {},
    taskPrompt:
      typeof raw.task_prompt === "string"
        ? raw.task_prompt
        : typeof raw.taskPrompt === "string"
          ? raw.taskPrompt
          : null,
    judgeRubric:
      typeof raw.judge_rubric === "string"
        ? raw.judge_rubric
        : typeof raw.judgeRubric === "string"
          ? raw.judgeRubric
          : null,
    exampleOutputs: Array.isArray(raw.example_outputs)
      ? (raw.example_outputs as Array<{ output: string; score: number; reasoning: string }>)
      : Array.isArray(raw.exampleOutputs)
        ? (raw.exampleOutputs as Array<{ output: string; score: number; reasoning: string }>)
        : null,
    outputFormat:
      typeof raw.output_format === "string"
        ? raw.output_format
        : typeof raw.outputFormat === "string"
          ? raw.outputFormat
          : null,
    referenceContext:
      typeof raw.reference_context === "string"
        ? raw.reference_context
        : typeof raw.referenceContext === "string"
          ? raw.referenceContext
          : null,
    contextPreparation:
      typeof raw.context_preparation === "string"
        ? raw.context_preparation
        : typeof raw.contextPreparation === "string"
          ? raw.contextPreparation
          : null,
    maxRounds:
      typeof raw.max_rounds === "number"
        ? raw.max_rounds
        : typeof raw.maxRounds === "number"
          ? raw.maxRounds
          : null,
    qualityThreshold:
      typeof raw.quality_threshold === "number"
        ? raw.quality_threshold
        : typeof raw.qualityThreshold === "number"
          ? raw.qualityThreshold
          : null,
  };
  return data;
}

export function exportStrategyPackage(opts: {
  scenarioName: string;
  artifacts: ArtifactStore;
  store: SQLiteStore;
}): Record<string, unknown> {
  const persisted = readPackageMetadata(opts.artifacts.knowledgeRoot, opts.scenarioName);
  const playbook = opts.artifacts.readPlaybook(opts.scenarioName);
  const bestGeneration = opts.store.getBestGenerationForScenario(opts.scenarioName);
  const completedRuns = opts.store.countCompletedRuns(opts.scenarioName);
  const persistedMeta =
    persisted.metadata && typeof persisted.metadata === "object" && !Array.isArray(persisted.metadata)
      ? persisted.metadata
      : {};

  const pkg = new SkillPackage({
    scenarioName: opts.scenarioName,
    displayName: displayNameForScenario(opts.scenarioName),
    description: descriptionForScenario(opts.scenarioName),
    playbook,
    lessons: lessonsFromPlaybook(playbook),
    bestStrategy: bestStrategyForScenario(opts.store, opts.scenarioName, persisted),
    bestScore: bestGeneration?.best_score ?? persisted.best_score ?? 0,
    bestElo: bestGeneration?.elo ?? persisted.best_elo ?? 1500,
    hints: hintsFromPlaybook(playbook),
    harness: harnessForScenario(opts.artifacts.knowledgeRoot, opts.scenarioName),
    metadata: {
      ...persistedMeta,
      completed_runs: Math.max(
        completedRuns,
        typeof persistedMeta.completed_runs === "number" ? persistedMeta.completed_runs : 0,
      ),
      has_snapshot:
        bestGeneration != null
        || Boolean(
          typeof persistedMeta.has_snapshot === "boolean"
            ? persistedMeta.has_snapshot
            : false,
        ),
      source_run_id:
        bestGeneration?.run_id
        ?? (typeof persistedMeta.source_run_id === "string" ? persistedMeta.source_run_id : null),
    },
  });

  return {
    format_version: PACKAGE_FORMAT_VERSION,
    ...pkg.toDict(),
    skill_markdown: pkg.toSkillMarkdown(),
  };
}

export function importStrategyPackage(opts: {
  rawPackage: Record<string, unknown>;
  artifacts: ArtifactStore;
  skillsRoot: string;
  scenarioOverride?: string;
  conflictPolicy?: ConflictPolicy;
}): ImportStrategyPackageResult {
  const conflictPolicy = opts.conflictPolicy ?? "overwrite";
  const pkg = coercePackage(opts.rawPackage, opts.scenarioOverride);
  const result: ImportStrategyPackageResult = {
    scenario: pkg.scenarioName,
    playbookWritten: false,
    harnessWritten: [],
    harnessSkipped: [],
    skillWritten: false,
    metadataWritten: false,
    conflictPolicy,
  };

  const existingPlaybook = opts.artifacts.readPlaybook(pkg.scenarioName);
  const isExistingPlaybookEmpty = !existingPlaybook || existingPlaybook === EMPTY_PLAYBOOK_SENTINEL;
  const shouldWritePlaybook =
    pkg.playbook
    && (
      conflictPolicy === "overwrite"
      || (conflictPolicy === "merge" && isExistingPlaybookEmpty)
      || (conflictPolicy === "skip" && isExistingPlaybookEmpty)
    );

  if (shouldWritePlaybook) {
    opts.artifacts.writePlaybook(pkg.scenarioName, pkg.playbook);
    result.playbookWritten = true;
  }

  const harnessStore = new HarnessStore(opts.artifacts.knowledgeRoot, pkg.scenarioName);
  for (const [name, source] of Object.entries(pkg.harness ?? {})) {
    const existing = harnessStore.read(name);
    if (conflictPolicy === "overwrite" || existing == null) {
      harnessStore.writeVersioned(name, source, 0);
      result.harnessWritten.push(name);
    } else {
      result.harnessSkipped.push(name);
    }
  }

  const metadataPayload: PersistedPackageMetadata = {
    format_version: pkg.formatVersion ?? PACKAGE_FORMAT_VERSION,
    best_strategy: pkg.bestStrategy,
    best_score: pkg.bestScore,
    best_elo: pkg.bestElo,
    metadata:
      pkg.metadata && typeof pkg.metadata === "object" && !Array.isArray(pkg.metadata)
        ? pkg.metadata
        : {},
  };
  writePackageMetadata(opts.artifacts.knowledgeRoot, pkg.scenarioName, metadataPayload);
  result.metadataWritten = true;

  const skillDir = join(opts.skillsRoot, `${pkg.scenarioName.replace(/_/g, "-")}-ops`);
  const skillPath = join(skillDir, "SKILL.md");
  const skillMarkdown = typeof opts.rawPackage.skill_markdown === "string"
    ? opts.rawPackage.skill_markdown
    : new SkillPackage(pkg).toSkillMarkdown();
  const shouldWriteSkill =
    conflictPolicy === "overwrite"
    || !existsSync(skillPath)
    || (conflictPolicy === "merge" && !existsSync(skillPath))
    || (conflictPolicy === "skip" && !existsSync(skillPath));
  if (shouldWriteSkill) {
    mkdirSync(skillDir, { recursive: true });
    writeFileSync(skillPath, skillMarkdown.trimEnd() + "\n", "utf-8");
    result.skillWritten = true;
  }

  return result;
}
