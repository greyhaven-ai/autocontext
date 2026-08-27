import { executeScenarioRevision } from "./scenario-revision-execution.js";
import {
  acquireProviderIsolation,
  closeProviderIsolation,
  NO_TOOLS_PROVIDER_ISOLATION,
} from "../providers/provider-isolation.js";
import type {
  ReviseSpecOpts,
  RevisionResult,
} from "./scenario-revision-contracts.js";
import { buildRevisionPrompt } from "./scenario-revision-prompt-workflow.js";
import {
  partitionScenarioRevisionSpec,
  restoreScenarioRevisionSpec,
} from "./scenario-revision-visibility.js";

export async function reviseSpec(opts: ReviseSpecOpts): Promise<RevisionResult> {
  const visibility = partitionScenarioRevisionSpec(
    opts.family,
    opts.currentSpec,
  );
  const prompt = buildRevisionPrompt({
    currentSpec: opts.currentSpec,
    feedback: opts.feedback,
    family: opts.family,
    judgeResult: opts.judgeResult,
  });

  const acquiredProvider =
    requiresNoToolsScenarioRevision(opts.family, opts.currentSpec)
      ? acquireProviderIsolation(opts.provider, NO_TOOLS_PROVIDER_ISOLATION)
      : { provider: opts.provider, owned: false };
  let revision;
  try {
    revision = await executeScenarioRevision({
      currentSpec: visibility.providerVisibleSpec,
      validationSpec: opts.currentSpec,
      family: opts.family,
      prompt,
      provider: acquiredProvider.provider,
      model: opts.model,
    });
  } finally {
    closeProviderIsolation(acquiredProvider);
  }

  return {
    ...revision,
    original: { ...opts.currentSpec },
    revised: restoreScenarioRevisionSpec(
      opts.family,
      revision.revised,
      visibility.immutableSpec,
    ),
  };
}

function requiresNoToolsScenarioRevision(
  family: string,
  currentSpec: Record<string, unknown>,
): boolean {
  if (family !== "agent_task") return false;
  const contractVersion =
    currentSpec.improvementTaskContractVersion ??
    currentSpec.improvement_task_contract_version;
  const hasEvaluatorContext = [
    currentSpec.evaluationContext,
    currentSpec.evaluation_context,
  ].some((value) => typeof value === "string" && value.trim().length > 0);
  const hasEvaluatorReference =
    Object.prototype.hasOwnProperty.call(currentSpec, "evaluationContextRef") ||
    Object.prototype.hasOwnProperty.call(currentSpec, "evaluation_context_ref");
  return contractVersion === 1 || hasEvaluatorContext || hasEvaluatorReference;
}
