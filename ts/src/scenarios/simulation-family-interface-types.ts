export interface SimulationInterface {
  describeScenario(): string;
  describeEnvironment(): unknown;
  initialState(seed?: number): Record<string, unknown>;
  getAvailableActions(state: Record<string, unknown>): unknown[];
  executeAction(state: Record<string, unknown>, action: unknown): [unknown, Record<string, unknown>];
  isTerminal(state: Record<string, unknown>): boolean;
  evaluateTrace(trace: unknown, finalState: Record<string, unknown>): unknown;
  getRubric(): string;
}

export interface NegotiationInterface extends SimulationInterface {
  getHiddenPreferences(state: Record<string, unknown>): unknown;
  getRounds(state: Record<string, unknown>): unknown[];
  getOpponentModel(state: Record<string, unknown>): unknown | null;
  updateOpponentModel(state: Record<string, unknown>, model: unknown): Record<string, unknown>;
  evaluateNegotiation(state: Record<string, unknown>): unknown;
}

export interface InvestigationInterface extends SimulationInterface {
  getEvidencePool(state: Record<string, unknown>): unknown[];
  evaluateEvidenceChain(chain: unknown, state: Record<string, unknown>): unknown;
  evaluateDiagnosis(diagnosis: string, evidenceChain: unknown, state: Record<string, unknown>): unknown;
}

export interface WorkflowInterface extends SimulationInterface {
  getWorkflowSteps(): unknown[];
  executeStep(state: Record<string, unknown>, step: unknown): unknown;
  executeCompensation(state: Record<string, unknown>, step: unknown): unknown;
  getSideEffects(state: Record<string, unknown>): unknown[];
  evaluateWorkflow(state: Record<string, unknown>): unknown;
}

export interface SchemaEvolutionInterface extends SimulationInterface {
  getMutations(): unknown[];
  getSchemaVersion(state: Record<string, unknown>): number;
  getMutationLog(state: Record<string, unknown>): unknown[];
  applyMutation(state: Record<string, unknown>, mutation: unknown): Record<string, unknown>;
  checkContextValidity(state: Record<string, unknown>, assumptions: string[]): unknown[];
  evaluateAdaptation(state: Record<string, unknown>): unknown;
}

export interface ToolFragilityInterface extends SimulationInterface {
  getToolContracts(state: Record<string, unknown>): unknown[];
  getDriftLog(state: Record<string, unknown>): unknown[];
  injectDrift(state: Record<string, unknown>, drift: unknown): Record<string, unknown>;
  attributeFailure(state: Record<string, unknown>, step: number, error: string): unknown;
  evaluateFragility(state: Record<string, unknown>): unknown;
}

export interface OperatorLoopInterface extends SimulationInterface {
  getEscalationLog(state: Record<string, unknown>): unknown[];
  getClarificationLog(state: Record<string, unknown>): unknown[];
  escalate(state: Record<string, unknown>, event: unknown): Record<string, unknown>;
  requestClarification(state: Record<string, unknown>, request: unknown): Record<string, unknown>;
  evaluateJudgment(state: Record<string, unknown>): unknown;
}

export interface CoordinationInterface extends SimulationInterface {
  getWorkerContexts(state: Record<string, unknown>): unknown[];
  getHandoffLog(state: Record<string, unknown>): unknown[];
  recordHandoff(state: Record<string, unknown>, handoff: unknown): Record<string, unknown>;
  mergeOutputs(state: Record<string, unknown>, workerOutputs: Record<string, string>): Record<string, unknown>;
  evaluateCoordination(state: Record<string, unknown>): unknown;
}
