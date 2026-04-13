import { executeScenarioRevision } from "./scenario-revision-execution.js";
import type {
  ReviseSpecOpts,
  RevisionResult,
} from "./scenario-revision-contracts.js";
import { buildRevisionPrompt } from "./scenario-revision-prompt-workflow.js";

export async function reviseSpec(opts: ReviseSpecOpts): Promise<RevisionResult> {
  const prompt = buildRevisionPrompt({
    currentSpec: opts.currentSpec,
    feedback: opts.feedback,
    family: opts.family,
    judgeResult: opts.judgeResult,
  });

  return executeScenarioRevision({
    currentSpec: opts.currentSpec,
    family: opts.family,
    prompt,
    provider: opts.provider,
    model: opts.model,
  });
}
