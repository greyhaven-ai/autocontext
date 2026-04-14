export interface CollectedInvestigationEvidenceItem {
  id: string;
  content: string;
  isRedHerring: boolean;
  relevance: number;
}

export interface InvestigationExecutionResult {
  stepsExecuted: number;
  collectedEvidence: CollectedInvestigationEvidenceItem[];
  finalState: Record<string, unknown>;
}

export async function executeGeneratedInvestigation(opts: {
  source: string;
  maxSteps?: number;
}): Promise<InvestigationExecutionResult> {
  const moduleObj = { exports: {} as Record<string, unknown> };
  const fn = new Function("module", "exports", opts.source);
  fn(moduleObj, moduleObj.exports);
  const scenario = (moduleObj.exports as {
    scenario: Record<string, (...args: unknown[]) => unknown>;
  }).scenario;

  let state = scenario.initialState(42) as Record<string, unknown>;
  const limit = opts.maxSteps ?? 8;
  let steps = 0;

  while (steps < limit) {
    const terminal = scenario.isTerminal(state) as boolean;
    if (terminal) break;
    const actions = scenario.getAvailableActions(state) as Array<{ name: string }>;
    if (!actions || actions.length === 0) break;
    const actionResult = scenario.executeAction(state, {
      name: actions[0].name,
      parameters: {},
    }) as {
      result: Record<string, unknown>;
      state: Record<string, unknown>;
    };
    state = actionResult.state;
    steps += 1;
  }

  const collectedEvidence = ((state.collectedEvidence ?? []) as Array<Record<string, unknown>>)
    .map((item, index) => ({
      id: typeof item.id === "string" ? item.id : `collected_${index}`,
      content:
        typeof item.content === "string"
          ? item.content
          : typeof item.summary === "string"
            ? item.summary
            : typeof item.id === "string"
              ? item.id
              : "unknown",
      isRedHerring: !!item.isRedHerring,
      relevance: typeof item.relevance === "number" ? item.relevance : 0,
    }));

  return { stepsExecuted: steps, collectedEvidence, finalState: state };
}
