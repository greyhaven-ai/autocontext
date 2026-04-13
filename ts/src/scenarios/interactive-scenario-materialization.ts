import type { MaterializeResult } from "./materialize.js";
import { materializeScenario } from "./materialize.js";
import type { ScenarioDraft } from "./draft-workflow.js";

export async function persistInteractiveScenarioDraft(opts: {
  draft: ScenarioDraft;
  knowledgeRoot: string;
}): Promise<MaterializeResult> {
  return materializeScenario({
    name: opts.draft.preview.name,
    family: opts.draft.preview.family,
    spec: {
      ...opts.draft.preview.spec,
      intent_confidence: opts.draft.validation.confidence,
      intent_issues: opts.draft.validation.issues,
    },
    knowledgeRoot: opts.knowledgeRoot,
  });
}
