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

export class InteractiveScenarioSession {
  readonly #knowledgeRoot: string;
  readonly #humanizeName: (name: string) => string;
  readonly #deps: InteractiveScenarioSessionDeps;
  #pendingScenario: ScenarioDraft | null = null;

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
    return this.#pendingScenario;
  }

  async createScenario(opts: {
    description: string;
    provider: LLMProvider;
  }): Promise<ScenarioPreviewInfo> {
    const created = await (
      this.#deps.createScenarioFromDescription ?? createScenarioFromDescription
    )(opts.description, opts.provider);
    const draft = buildScenarioDraft({ description: opts.description, created });
    this.#pendingScenario = draft;
    return this.#buildPreview(draft);
  }

  async createTask(opts: {
    contract: ImprovementTaskContract;
    sourceContents: TaskDataSourceContent[];
  }): Promise<ScenarioPreviewInfo> {
    const spec = compileResolvedImprovementTaskContract(opts.contract, opts.sourceContents);
    const name = resolveStructuredTaskScenarioName({
      knowledgeRoot: this.#knowledgeRoot,
      baseName: deriveScenarioName(opts.contract.objective),
      spec,
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
    this.#pendingScenario = draft;
    return this.#buildPreview(draft);
  }

  async reviseScenario(opts: {
    feedback: string;
    provider: LLMProvider;
  }): Promise<ScenarioPreviewInfo> {
    const draft = this.#requirePendingScenario();
    if (
      draft.preview.spec.improvementTaskContractVersion === 1 ||
      draft.preview.spec.improvement_task_contract_version === 1
    ) {
      throw new Error(
        "Structured task contracts cannot be revised in place. Cancel setup, edit the mission or data roles, and compile a new task.",
      );
    }
    const visibility = partitionScenarioRevisionSpec(draft.preview.family, draft.preview.spec);
    const revision = await (this.#deps.reviseSpec ?? reviseSpec)({
      currentSpec: visibility.providerVisibleSpec,
      feedback: opts.feedback,
      family: draft.preview.family,
      provider: opts.provider,
    });
    if (!revision.changesApplied) {
      throw new Error(revision.error ?? "Scenario revision failed.");
    }

    const revisedDraft = reviseScenarioDraft({
      draft,
      revisedSpec: restoreScenarioRevisionSpec(
        draft.preview.family,
        revision.revised,
        visibility.immutableSpec,
      ),
    });
    this.#pendingScenario = revisedDraft;
    return this.#buildPreview(revisedDraft);
  }

  cancelScenario(): void {
    this.#pendingScenario = null;
  }

  async confirmScenario(): Promise<InteractiveScenarioReadyInfo> {
    const pending = this.#requirePendingScenario();
    if (!pending.validation.valid) {
      throw new Error(pending.validation.issues.join("; "));
    }

    const persisted = await (
      this.#deps.persistInteractiveScenarioDraft ?? persistInteractiveScenarioDraft
    )({
      draft: pending,
      knowledgeRoot: this.#knowledgeRoot,
    });
    if (!persisted.persisted) {
      throw new Error(persisted.errors.join("; ") || "Scenario persistence failed.");
    }

    this.#pendingScenario = null;
    return { name: pending.preview.name, testScores: [] };
  }

  #requirePendingScenario(): ScenarioDraft {
    if (!this.#pendingScenario) {
      throw new Error("No scenario preview is pending. Create a scenario first.");
    }
    return this.#pendingScenario;
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
}): string {
  const customScenariosRoot = join(opts.knowledgeRoot, "_custom_scenarios");
  if (!existsSync(join(customScenariosRoot, opts.baseName))) return opts.baseName;

  const digest = createHash("sha256")
    .update(JSON.stringify(canonicalizeForHash(opts.spec)))
    .digest("hex");
  return `${opts.baseName}_${digest.slice(0, 12)}`;
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
