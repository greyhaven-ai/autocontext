import { executeScenarioRevision } from "./scenario-revision-execution.js";
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
    currentSpec: visibility.providerVisibleSpec,
    feedback: opts.feedback,
    family: opts.family,
    judgeResult: opts.judgeResult,
  });

  const revision = await executeScenarioRevision({
    currentSpec: visibility.providerVisibleSpec,
    family: opts.family,
    prompt,
    provider: opts.provider,
    model: opts.model,
  });

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
