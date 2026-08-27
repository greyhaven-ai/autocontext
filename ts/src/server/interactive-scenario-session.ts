import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { join } from "node:path";

import type { LLMProvider } from "../types/index.js";
import {
  buildScenarioDraft,
  buildScenarioPreviewInfo,
  reviseScenarioDraft,
  type ScenarioDraft,
  type ScenarioPreviewInfo,
} from "../scenarios/draft-workflow.js";
import {
  compileResolvedImprovementTaskContract,
  type ImprovementTaskContract,
} from "../scenarios/improvement-task-contract.js";
import type { TaskDataSourceContent } from "../scenarios/task-data-source.js";
import type { CreatedScenarioResult } from "../scenarios/scenario-creator.js";
import {
  createScenarioFromDescription,
  deriveScenarioName,
} from "../scenarios/scenario-creator.js";
import type { RevisionResult } from "../scenarios/scenario-revision.js";
import { reviseSpec } from "../scenarios/scenario-revision.js";
import {
  partitionScenarioRevisionSpec,
  restoreScenarioRevisionSpec,
} from "../scenarios/scenario-revision-visibility.js";
import { persistInteractiveScenarioDraft } from "../scenarios/interactive-scenario-materialization.js";
import type { MaterializeResult } from "../scenarios/materialize.js";

export interface InteractiveScenarioReadyInfo {
  name: string;
  testScores: number[];
}

export interface InteractiveScenarioSessionDeps {
  createScenarioFromDescription?: (
    description: string,
    provider: LLMProvider,
  ) => Promise<CreatedScenarioResult>;
  reviseSpec?: (opts: {
    currentSpec: Record<string, unknown>;
    feedback: string;
    family: string;
    provider: LLMProvider;
  }) => Promise<RevisionResult>;
  persistInteractiveScenarioDraft?: (opts: {
    draft: ScenarioDraft;
    knowledgeRoot: string;
  }) => Promise<MaterializeResult>;
}

export type InteractiveScenarioScope = object;

const DEFAULT_INTERACTIVE_SCENARIO_SCOPE: InteractiveScenarioScope = Object.freeze({});

export class InteractiveScenarioSession {
  readonly #knowledgeRoot: string;
  readonly #humanizeName: (name: string) => string;
  readonly #deps: InteractiveScenarioSessionDeps;
  readonly #pendingScenarios = new Map<InteractiveScenarioScope, ScenarioDraft>();
  readonly #scopeVersions = new WeakMap<InteractiveScenarioScope, number>();
  readonly #persistingScenarioNames = new Set<string>();

  constructor(opts: {
    knowledgeRoot: string;
    humanizeName: (name: string) => string;
    deps?: InteractiveScenarioSessionDeps;
  }) {
    this.#knowledgeRoot = opts.knowledgeRoot;
    this.#humanizeName = opts.humanizeName;
    this.#deps = opts.deps ?? {};
  }

  get pendingScenario(): ScenarioDraft | null {
    return this.#pendingScenarios.get(DEFAULT_INTERACTIVE_SCENARIO_SCOPE) ?? null;
  }

  async createScenario(opts: {
    description: string;
    provider: LLMProvider;
    scope?: InteractiveScenarioScope;
  }): Promise<ScenarioPreviewInfo> {
    const scope = opts.scope ?? DEFAULT_INTERACTIVE_SCENARIO_SCOPE;
    const scopeVersion = this.#advanceScopeVersion(scope);
    this.#pendingScenarios.delete(scope);
    const created = await (
      this.#deps.createScenarioFromDescription ?? createScenarioFromDescription
    )(opts.description, opts.provider);
    this.#assertScopeVersion(scope, scopeVersion);
    const scopedCreated = {
      ...created,
      name: resolveReservedPendingScenarioName({
        baseName: created.name,
        spec: created.spec,
        reservedNames: this.#reservedScenarioNames(scope),
      }),
    };
    const draft = buildScenarioDraft({ description: opts.description, created: scopedCreated });
    this.#pendingScenarios.set(scope, draft);
    return this.#buildPreview(draft);
  }

  async createTask(opts: {
    contract: ImprovementTaskContract;
    sourceContents: TaskDataSourceContent[];
    scope?: InteractiveScenarioScope;
  }): Promise<ScenarioPreviewInfo> {
    const scope = opts.scope ?? DEFAULT_INTERACTIVE_SCENARIO_SCOPE;
    this.#advanceScopeVersion(scope);
    this.#pendingScenarios.delete(scope);
    const spec = compileResolvedImprovementTaskContract(opts.contract, opts.sourceContents);
    const name = resolveStructuredTaskScenarioName({
      knowledgeRoot: this.#knowledgeRoot,
      baseName: deriveScenarioName(opts.contract.objective),
      spec,
      reservedNames: this.#reservedScenarioNames(scope),
    });
    const created: CreatedScenarioResult = {
      name,
      family: "agent_task",
      spec: {
        ...spec,
        rubric: spec.judgeRubric,
        description: opts.contract.deliverable.description,
      },
    };
    const draft = buildScenarioDraft({
      description: opts.contract.objective,
      created,
    });
    this.#pendingScenarios.set(scope, draft);
    return this.#buildPreview(draft);
  }

  async reviseScenario(opts: {
    feedback: string;
    provider: LLMProvider;
    scope?: InteractiveScenarioScope;
  }): Promise<ScenarioPreviewInfo> {
    const scope = opts.scope ?? DEFAULT_INTERACTIVE_SCENARIO_SCOPE;
    const draft = this.#requirePendingScenario(scope);
    if (
      draft.preview.spec.improvementTaskContractVersion === 1 ||
      draft.preview.spec.improvement_task_contract_version === 1
    ) {
      throw new Error(
        "Structured task contracts cannot be revised in place. Cancel setup, edit the mission or data roles, and compile a new task.",
      );
    }
    const scopeVersion = this.#advanceScopeVersion(scope);
    const visibility = partitionScenarioRevisionSpec(draft.preview.family, draft.preview.spec);
    const revision = await (this.#deps.reviseSpec ?? reviseSpec)({
      currentSpec: draft.preview.spec,
      feedback: opts.feedback,
      family: draft.preview.family,
      provider: opts.provider,
    });
    if (!revision.changesApplied) {
      throw new Error(revision.error ?? "Scenario revision failed.");
    }
    this.#assertScopeVersion(scope, scopeVersion);
    if (this.#pendingScenarios.get(scope) !== draft) {
      throw new Error("Scenario setup was cancelled or superseded.");
    }

    const revisedDraft = reviseScenarioDraft({
      draft,
      revisedSpec: restoreScenarioRevisionSpec(
        draft.preview.family,
        revision.revised,
        visibility.immutableSpec,
      ),
    });
    this.#pendingScenarios.set(scope, revisedDraft);
    return this.#buildPreview(revisedDraft);
  }

  cancelScenario(scope = DEFAULT_INTERACTIVE_SCENARIO_SCOPE): void {
    this.#advanceScopeVersion(scope);
    this.#pendingScenarios.delete(scope);
  }

  async confirmScenario(
    scope = DEFAULT_INTERACTIVE_SCENARIO_SCOPE,
  ): Promise<InteractiveScenarioReadyInfo> {
    const pending = this.#requirePendingScenario(scope);
    if (!pending.validation.valid) {
      throw new Error(pending.validation.issues.join("; "));
    }
    const scopeVersion = this.#advanceScopeVersion(scope);
    this.#pendingScenarios.delete(scope);
    this.#persistingScenarioNames.add(pending.preview.name);

    try {
      const persisted = await (
        this.#deps.persistInteractiveScenarioDraft ?? persistInteractiveScenarioDraft
      )({
        draft: pending,
        knowledgeRoot: this.#knowledgeRoot,
      });
      if (!persisted.persisted) {
        throw new Error(persisted.errors.join("; ") || "Scenario persistence failed.");
      }
      return { name: pending.preview.name, testScores: [] };
    } catch (error) {
      if (
        this.#scopeVersions.get(scope) === scopeVersion &&
        !this.#pendingScenarios.has(scope)
      ) {
        this.#pendingScenarios.set(scope, pending);
      }
      throw error;
    } finally {
      this.#persistingScenarioNames.delete(pending.preview.name);
    }
  }

  #requirePendingScenario(scope: InteractiveScenarioScope): ScenarioDraft {
    const pending = this.#pendingScenarios.get(scope);
    if (!pending) {
      throw new Error("No scenario preview is pending. Create a scenario first.");
    }
    return pending;
  }

  #reservedScenarioNames(excludedScope: InteractiveScenarioScope): Set<string> {
    const reservedNames = new Set(this.#persistingScenarioNames);
    for (const [scope, draft] of this.#pendingScenarios) {
      if (scope !== excludedScope) reservedNames.add(draft.preview.name);
    }
    return reservedNames;
  }

  #advanceScopeVersion(scope: InteractiveScenarioScope): number {
    const nextVersion = (this.#scopeVersions.get(scope) ?? 0) + 1;
    this.#scopeVersions.set(scope, nextVersion);
    return nextVersion;
  }

  #assertScopeVersion(scope: InteractiveScenarioScope, expectedVersion: number): void {
    if (this.#scopeVersions.get(scope) !== expectedVersion) {
      throw new Error("Scenario setup was cancelled or superseded.");
    }
  }

  #buildPreview(draft: ScenarioDraft): ScenarioPreviewInfo {
    return buildScenarioPreviewInfo(draft, {
      humanizeName: this.#humanizeName,
    });
  }
}

function resolveStructuredTaskScenarioName(opts: {
  knowledgeRoot: string;
  baseName: string;
  spec: Record<string, unknown>;
  reservedNames?: ReadonlySet<string>;
}): string {
  const customScenariosRoot = join(opts.knowledgeRoot, "_custom_scenarios");
  const reservedNames = opts.reservedNames ?? new Set<string>();
  if (
    !existsSync(join(customScenariosRoot, opts.baseName)) &&
    !reservedNames.has(opts.baseName)
  ) {
    return opts.baseName;
  }

  const digest = scenarioSpecDigest(opts.spec);
  const contentAddressedName = `${opts.baseName}_${digest.slice(0, 12)}`;
  if (!reservedNames.has(contentAddressedName)) return contentAddressedName;

  let suffix = 2;
  let candidate = `${contentAddressedName}_${suffix}`;
  while (
    reservedNames.has(candidate) ||
    existsSync(join(customScenariosRoot, candidate))
  ) {
    suffix += 1;
    candidate = `${contentAddressedName}_${suffix}`;
  }
  return candidate;
}

function resolveReservedPendingScenarioName(opts: {
  baseName: string;
  spec: Record<string, unknown>;
  reservedNames: ReadonlySet<string>;
}): string {
  if (!opts.reservedNames.has(opts.baseName)) return opts.baseName;

  const contentAddressedName = `${opts.baseName}_${scenarioSpecDigest(opts.spec).slice(0, 12)}`;
  if (!opts.reservedNames.has(contentAddressedName)) return contentAddressedName;

  let suffix = 2;
  let candidate = `${contentAddressedName}_${suffix}`;
  while (opts.reservedNames.has(candidate)) {
    suffix += 1;
    candidate = `${contentAddressedName}_${suffix}`;
  }
  return candidate;
}

function scenarioSpecDigest(spec: Record<string, unknown>): string {
  return createHash("sha256")
    .update(JSON.stringify(canonicalizeForHash(spec)))
    .digest("hex");
}

function canonicalizeForHash(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalizeForHash);
  if (typeof value !== "object" || value === null) return value;
  return Object.fromEntries(
    Object.entries(value)
      .filter(([, entry]) => entry !== undefined)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, entry]) => [key, canonicalizeForHash(entry)]),
  );
}
