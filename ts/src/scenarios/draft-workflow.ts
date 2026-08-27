import { normalizePreviewThreshold } from "../analytics/number-utils.js";
import type { CreatedScenarioResult } from "./scenario-creator.js";
import { IntentValidator, type IntentValidationResult } from "./intent-validator.js";
import {
  partitionScenarioRevisionSpec,
  restoreScenarioRevisionSpec,
} from "./scenario-revision-visibility.js";

export interface ScenarioPreviewInfo {
  name: string;
  displayName: string;
  description: string;
  strategyParams: Array<{ name: string; description: string }>;
  scoringComponents: Array<{ name: string; description: string; weight: number }>;
  constraints: string[];
  winThreshold: number;
}

export interface ScenarioDraft {
  description: string;
  detectedFamily: string;
  preview: CreatedScenarioResult;
  validation: IntentValidationResult;
}

function readStringValue(spec: Record<string, unknown>, ...keys: string[]): string | undefined {
  for (const key of keys) {
    const value = spec[key];
    if (typeof value === "string" && value.trim().length > 0) {
      return value;
    }
  }
  return undefined;
}

function readFiniteNumberValue(
  spec: Record<string, unknown>,
  ...keys: string[]
): number | undefined {
  for (const key of keys) {
    const value = spec[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
  }
  return undefined;
}

function normalizeInteractivePreview(
  created: CreatedScenarioResult,
): CreatedScenarioResult {
  return created.family === "agent_task"
    ? created
    : { ...created, family: "agent_task" };
}

function validateDraft(
  description: string,
  preview: CreatedScenarioResult,
  validator: IntentValidator,
): IntentValidationResult {
  return validator.validate(description, {
    name: preview.name,
    taskPrompt: preview.spec.taskPrompt,
    rubric: preview.spec.rubric,
    description: preview.spec.description,
  });
}

export function buildScenarioDraft(opts: {
  description: string;
  created: CreatedScenarioResult;
  validator?: IntentValidator;
}): ScenarioDraft {
  const validator = opts.validator ?? new IntentValidator();
  const preview = normalizeInteractivePreview(opts.created);
  return {
    description: opts.description,
    detectedFamily: opts.created.family,
    preview,
    validation: validateDraft(opts.description, preview, validator),
  };
}

export function reviseScenarioDraft(opts: {
  draft: ScenarioDraft;
  revisedSpec: Record<string, unknown>;
  validator?: IntentValidator;
}): ScenarioDraft {
  const validator = opts.validator ?? new IntentValidator();
  const revisedSpec =
    opts.draft.preview.family === "agent_task"
      ? restoreScenarioRevisionSpec(
          "agent_task",
          opts.revisedSpec,
          partitionScenarioRevisionSpec(
            "agent_task",
            opts.draft.preview.spec,
          ).immutableSpec,
        )
      : opts.revisedSpec;
  const revisedPreview: CreatedScenarioResult = {
    ...opts.draft.preview,
    spec: {
      ...revisedSpec,
      taskPrompt: readStringValue(revisedSpec, "taskPrompt", "task_prompt")
        ?? opts.draft.preview.spec.taskPrompt,
      rubric: readStringValue(revisedSpec, "rubric", "judgeRubric", "judge_rubric")
        ?? opts.draft.preview.spec.rubric,
      description: readStringValue(revisedSpec, "description")
        ?? opts.draft.preview.spec.description,
    },
  };

  return {
    ...opts.draft,
    preview: revisedPreview,
    validation: validateDraft(opts.draft.description, revisedPreview, validator),
  };
}

export function buildScenarioPreviewInfo(
  draft: ScenarioDraft,
  opts?: { humanizeName?: (name: string) => string },
): ScenarioPreviewInfo {
  const constraints = draft.validation.valid
    ? [`Intent validated at ${(draft.validation.confidence * 100).toFixed(0)}% confidence.`]
    : [...draft.validation.issues];

  if (draft.detectedFamily !== draft.preview.family) {
    constraints.push(
      `Detected ${draft.detectedFamily} signals, but the interactive TS creator currently saves agent-task scaffolds only.`,
    );
  }

  const maxRounds = readFiniteNumberValue(
    draft.preview.spec,
    "maxRounds",
    "max_rounds",
  );
  if (maxRounds !== undefined) {
    constraints.push(`Improvement loop: up to ${maxRounds} attempts.`);
  }
  const qualityThreshold = readFiniteNumberValue(
    draft.preview.spec,
    "qualityThreshold",
    "quality_threshold",
  );

  return {
    name: draft.preview.name,
    displayName: opts?.humanizeName?.(draft.preview.name) ?? draft.preview.name,
    description: `${draft.preview.spec.description} [family: ${draft.preview.family}]`,
    strategyParams: [
      { name: "family", description: draft.preview.family },
      { name: "task_prompt", description: draft.preview.spec.taskPrompt },
    ],
    scoringComponents: [
      { name: "rubric", description: draft.preview.spec.rubric, weight: 1.0 },
    ],
    constraints,
    winThreshold:
      qualityThreshold === undefined
        ? normalizePreviewThreshold(draft.validation.confidence)
        : normalizePreviewThreshold(qualityThreshold),
  };
}
