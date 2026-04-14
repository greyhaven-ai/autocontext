import type { LLMProvider } from "../types/index.js";
import {
  buildScenarioDraft,
  buildScenarioPreviewInfo,
  reviseScenarioDraft,
  type ScenarioDraft,
  type ScenarioPreviewInfo,
} from "../scenarios/draft-workflow.js";
import type { CreatedScenarioResult } from "../scenarios/scenario-creator.js";
import { createScenarioFromDescription } from "../scenarios/scenario-creator.js";
import type { RevisionResult } from "../scenarios/scenario-revision.js";
import { reviseSpec } from "../scenarios/scenario-revision.js";
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
    const created = await (this.#deps.createScenarioFromDescription ?? createScenarioFromDescription)(
      opts.description,
      opts.provider,
    );
    const draft = buildScenarioDraft({ description: opts.description, created });
    this.#pendingScenario = draft;
    return this.#buildPreview(draft);
  }

  async reviseScenario(opts: {
    feedback: string;
    provider: LLMProvider;
  }): Promise<ScenarioPreviewInfo> {
    const draft = this.#requirePendingScenario();
    const revision = await (this.#deps.reviseSpec ?? reviseSpec)({
      currentSpec: draft.preview.spec,
      feedback: opts.feedback,
      family: draft.preview.family,
      provider: opts.provider,
    });
    if (!revision.changesApplied) {
      throw new Error(revision.error ?? "Scenario revision failed.");
    }

    const revisedDraft = reviseScenarioDraft({
      draft,
      revisedSpec: revision.revised,
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

    const persisted = await (this.#deps.persistInteractiveScenarioDraft ?? persistInteractiveScenarioDraft)({
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
