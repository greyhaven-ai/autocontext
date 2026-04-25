import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { SQLiteStore } from "../storage/index.js";
import { ArtifactStore, EMPTY_PLAYBOOK_SENTINEL } from "./artifact-store.js";
import { HarnessStore } from "./harness-store.js";
import {
  SkillPackage,
  type SkillPackageDict,
} from "./skill-package.js";
import {
  bestStrategyForScenario,
  descriptionForScenario,
  displayNameForScenario,
  readPackageMetadata,
  writePackageMetadata,
} from "./package-metadata.js";
import {
  harnessForScenario,
  hintsFromPlaybook,
  lessonsFromPlaybook,
} from "./package-content.js";
import { coercePackage } from "./package-coercion.js";
import { assertSafeScenarioId } from "./scenario-id.js";
import type {
  ConflictPolicy,
  ImportStrategyPackageResult,
  PersistedPackageMetadata,
  StrategyPackageData,
} from "./package-types.js";

const PACKAGE_FORMAT_VERSION = 1;

export type { ConflictPolicy, ImportStrategyPackageResult, StrategyPackageData } from "./package-types.js";

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

  return serializeSkillPackage(pkg, PACKAGE_FORMAT_VERSION);
}

export interface SerializedSkillPackageDict extends SkillPackageDict {
  format_version: number;
  skill_markdown: string;
}

export function serializeSkillPackage(
  pkg: SkillPackage,
  formatVersion = PACKAGE_FORMAT_VERSION,
): SerializedSkillPackageDict {
  return {
    format_version: formatVersion,
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
  assertSafeScenarioId(pkg.scenarioName, "scenario_name");
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
