import type { ScenarioFamilyName } from "../scenarios/families.js";
import type { SimulationRunResult } from "./summary.js";

export interface SimulationAction {
  name: string;
  parameters?: Record<string, unknown>;
}

export interface SimulationActionResult {
  result: Record<string, unknown>;
  state: Record<string, unknown>;
}

export interface SimulationResultContext {
  records: Array<{ result: { success: boolean } }>;
}

export interface SimulationScenario {
  initialState(seed: number): Record<string, unknown>;
  isTerminal(state: Record<string, unknown>): boolean;
  getAvailableActions(state: Record<string, unknown>): SimulationAction[];
  executeAction(
    state: Record<string, unknown>,
    action: SimulationAction,
  ): SimulationActionResult;
  getResult(
    state: Record<string, unknown>,
    context: SimulationResultContext,
  ): {
    score?: number;
    reasoning?: string;
    dimensionScores?: Record<string, number>;
  };
  requestClarification?: (
    state: Record<string, unknown>,
    payload: { question: string; urgency: string },
  ) => Record<string, unknown>;
  escalate?: (
    state: Record<string, unknown>,
    payload: { reason: string; severity: string; wasNecessary: boolean },
  ) => Record<string, unknown>;
  getWorkerContexts?: () => Array<Record<string, unknown>>;
  recordHandoff?: (
    state: Record<string, unknown>,
    fromWorker: string,
    toWorker: string,
    payload: Record<string, unknown>,
  ) => Record<string, unknown>;
  mergeOutputs?: (
    state: Record<string, unknown>,
    payload: Record<string, string[]>,
  ) => Record<string, unknown>;
}

export interface ExecuteSimulationFamilyOpts {
  seed: number;
  maxSteps?: number;
}

export function loadGeneratedSimulationScenario(
  source: string,
): SimulationScenario {
  const moduleObj = { exports: {} as Record<string, unknown> };
  const fn = new Function("module", "exports", source);
  fn(moduleObj, moduleObj.exports);
  return (moduleObj.exports as { scenario: SimulationScenario }).scenario;
}

export function executeSimulationFamily(
  scenario: SimulationScenario,
  family: ScenarioFamilyName,
  opts: ExecuteSimulationFamilyOpts,
): SimulationRunResult {
  switch (family) {
    case "operator_loop":
      return executeOperatorLoopSimulation(scenario, opts.seed, opts.maxSteps);
    case "coordination":
      return executeCoordinationSimulation(scenario, opts.seed, opts.maxSteps);
    default:
      return executeGenericSimulation(scenario, opts.seed, opts.maxSteps);
  }
}

function executeGenericSimulation(
  scenario: SimulationScenario,
  seed: number,
  maxSteps?: number,
): SimulationRunResult {
  let state = scenario.initialState(seed);
  const limit = maxSteps ?? 20;
  let steps = 0;
  const records: SimulationResultContext["records"] = [];

  while (steps < limit) {
    if (scenario.isTerminal(state)) {
      break;
    }
    const actions = scenario.getAvailableActions(state);
    if (!actions || actions.length === 0) {
      break;
    }
    const actionResult = scenario.executeAction(state, {
      name: actions[0].name,
      parameters: {},
    });
    records.push({ result: { success: !!actionResult.result?.success } });
    state = actionResult.state;
    steps++;
  }

  return normalizeSimulationRunResult(scenario.getResult(state, { records }));
}

function executeOperatorLoopSimulation(
  scenario: SimulationScenario,
  seed: number,
  maxSteps?: number,
): SimulationRunResult {
  let state = scenario.initialState(seed);
  const limit = maxSteps ?? 20;
  let steps = 0;
  let requestedClarification = false;
  let escalated = false;
  const records: SimulationResultContext["records"] = [];

  while (steps < limit) {
    if (scenario.isTerminal(state)) {
      break;
    }

    if (!requestedClarification && typeof scenario.requestClarification === "function") {
      state = scenario.requestClarification(state, {
        question: "Clarify the current uncertainty before continuing.",
        urgency: "medium",
      });
      requestedClarification = true;
    }

    const actions = scenario.getAvailableActions(state);
    if (!actions || actions.length === 0) {
      break;
    }

    const action = {
      name: String(actions[0]?.name ?? "unknown"),
      parameters:
        actions[0]?.parameters && typeof actions[0].parameters === "object"
          ? actions[0].parameters
          : {},
    };
    const actionResult = scenario.executeAction(state, action);
    records.push({ result: { success: !!actionResult.result?.success } });
    state = actionResult.state ?? state;

    const situations = Array.isArray(state.situationsRequiringEscalation)
      ? (state.situationsRequiringEscalation as Array<Record<string, unknown>>)
      : [];
    const latest = situations[situations.length - 1];
    if (latest && typeof scenario.escalate === "function") {
      state = scenario.escalate(state, {
        reason: String(latest.reason ?? "action failure"),
        severity: String(latest.severity ?? "high"),
        wasNecessary: true,
      });
      escalated = true;
    }
    steps++;
  }

  if (!escalated && typeof scenario.escalate === "function") {
    state = scenario.escalate(state, {
      reason: "Mandatory operator review checkpoint.",
      severity: "low",
      wasNecessary: true,
    });
  }

  return normalizeSimulationRunResult(scenario.getResult(state, { records }));
}

function executeCoordinationSimulation(
  scenario: SimulationScenario,
  seed: number,
  maxSteps?: number,
): SimulationRunResult {
  let state = scenario.initialState(seed);
  const limit = maxSteps ?? 20;
  let steps = 0;
  let workerIndex = 0;
  const records: SimulationResultContext["records"] = [];
  const workerContexts =
    typeof scenario.getWorkerContexts === "function"
      ? scenario.getWorkerContexts()
      : [];
  const workerIds = workerContexts.map((worker, index) =>
    String(worker.workerId ?? worker.id ?? `worker_${index + 1}`),
  );

  while (steps < limit) {
    if (scenario.isTerminal(state)) {
      break;
    }

    const actions = scenario.getAvailableActions(state);
    if (!actions || actions.length === 0) {
      break;
    }

    const action = {
      name: String(actions[0]?.name ?? "unknown"),
      parameters:
        actions[0]?.parameters && typeof actions[0].parameters === "object"
          ? actions[0].parameters
          : {},
    };

    if (workerIds.length > 1 && typeof scenario.recordHandoff === "function") {
      const fromWorker = workerIds[workerIndex % workerIds.length];
      const toWorker = workerIds[(workerIndex + 1) % workerIds.length];
      state = scenario.recordHandoff(state, fromWorker, toWorker, {
        action: action.name,
        step: steps + 1,
      });
    }

    const actionResult = scenario.executeAction(state, action);
    records.push({ result: { success: !!actionResult.result?.success } });
    state = actionResult.state ?? state;

    if (workerIds.length > 0 && typeof scenario.mergeOutputs === "function") {
      const workerId = workerIds[workerIndex % workerIds.length];
      state = scenario.mergeOutputs(state, {
        [workerId]: [String(actionResult.result?.output ?? action.name)],
      });
    }

    workerIndex++;
    steps++;
  }

  return normalizeSimulationRunResult(scenario.getResult(state, { records }));
}

function normalizeSimulationRunResult(result: {
  score?: number;
  reasoning?: string;
  dimensionScores?: Record<string, number>;
}): SimulationRunResult {
  return {
    score: result.score ?? 0,
    reasoning: result.reasoning ?? "",
    dimensionScores: result.dimensionScores ?? {},
  };
}
