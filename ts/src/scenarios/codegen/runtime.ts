/**
 * ScenarioRuntime — executes LLM-generated scenario code in a secure V8 isolate.
 *
 * Wraps secure-exec's NodeRuntime to load generated JS scenario source,
 * validate it implements the expected family interface, and expose methods
 * back to the host process.
 *
 * AC-436
 */

import {
  NodeRuntime,
  createNodeDriver,
  createNodeRuntimeDriverFactory,
} from "secure-exec";
import type { ScenarioFamilyName } from "../families.js";

export interface ScenarioRuntimeOpts {
  /** Memory limit in MB (default: 64) */
  memoryLimit?: number;
  /** CPU time limit in ms (default: 10000) */
  cpuTimeLimitMs?: number;
}

export interface ScenarioProxy {
  /** Call a method on the sandboxed scenario. */
  call<T = unknown>(method: string, ...args: unknown[]): Promise<T>;
  /** The family this proxy was created for. */
  family: ScenarioFamilyName;
  /** The scenario name. */
  name: string;
  /** Dispose the underlying runtime. */
  dispose(): void;
}

/**
 * Expected methods per family — used to validate generated code exports.
 */
const REQUIRED_METHODS: Record<string, readonly string[]> = {
  simulation: [
    "describeScenario", "describeEnvironment", "initialState",
    "getAvailableActions", "executeAction", "isTerminal", "getResult",
  ],
  agent_task: [
    "getTaskPrompt", "evaluateOutput", "getRubric", "initialState", "describeTask",
  ],
  artifact_editing: [
    "describeTask", "getRubric", "initialArtifacts", "getEditPrompt", "validateArtifact",
  ],
  investigation: [
    "describeScenario", "describeEnvironment", "initialState",
    "getAvailableActions", "executeAction", "isTerminal", "getResult",
    "getEvidencePool", "evaluateEvidenceChain", "evaluateDiagnosis",
  ],
  workflow: [
    "describeScenario", "describeEnvironment", "initialState",
    "getAvailableActions", "executeAction", "isTerminal", "getResult",
    "getWorkflowSteps", "executeStep", "executeCompensation", "getSideEffects",
  ],
  negotiation: [
    "describeScenario", "describeEnvironment", "initialState",
    "getAvailableActions", "executeAction", "isTerminal", "getResult",
    "getHiddenPreferences", "getRounds", "getOpponentModel", "updateOpponentModel",
  ],
  schema_evolution: [
    "describeScenario", "describeEnvironment", "initialState",
    "getAvailableActions", "executeAction", "isTerminal", "getResult",
    "getMutations", "getSchemaVersion", "getMutationLog", "applyMutation",
  ],
  tool_fragility: [
    "describeScenario", "describeEnvironment", "initialState",
    "getAvailableActions", "executeAction", "isTerminal", "getResult",
    "getToolContracts", "getDriftLog", "injectDrift", "attributeFailure",
  ],
  coordination: [
    "describeScenario", "describeEnvironment", "initialState",
    "getAvailableActions", "executeAction", "isTerminal", "getResult",
    "getWorkerContexts", "getHandoffLog", "recordHandoff", "mergeOutputs",
  ],
};

export class CodegenUnsupportedFamilyError extends Error {
  readonly family: string;
  constructor(family: string) {
    super(
      `Scenario family '${family}' is not supported for codegen execution. ` +
      (family === "operator_loop"
        ? "operator_loop scenarios are intentionally not scaffolded into executable runtimes; " +
          "use family metadata, datasets, tools, or live-agent experiments instead."
        : family === "game"
        ? "Built-in game scenarios should be used directly from SCENARIO_REGISTRY."
        : `No codegen pipeline registered for '${family}'.`),
    );
    this.family = family;
  }
}

/**
 * Build the sandbox wrapper code that loads the generated scenario source
 * and exposes an RPC-style call interface via module.exports.
 */
function buildSandboxWrapper(scenarioSource: string): string {
  return `
// --- Generated scenario code ---
${scenarioSource}
// --- End generated scenario code ---

// The generated code must assign a scenario object to module.exports.scenario
// or export individual methods.
const scenario = module.exports.scenario || module.exports;

// Validate scenario is an object with callable methods
if (!scenario || typeof scenario !== 'object') {
  throw new Error('Generated scenario must export an object with methods');
}

module.exports = { scenario };
`;
}

/**
 * Create a ScenarioRuntime that can load and execute generated scenario code.
 */
export class ScenarioRuntime {
  private runtime: NodeRuntime;
  private opts: ScenarioRuntimeOpts;

  constructor(opts: ScenarioRuntimeOpts = {}) {
    this.opts = opts;
    this.runtime = new NodeRuntime({
      systemDriver: createNodeDriver(),
      runtimeDriverFactory: createNodeRuntimeDriverFactory(),
      memoryLimit: opts.memoryLimit ?? 64,
      cpuTimeLimitMs: opts.cpuTimeLimitMs ?? 10_000,
    });
  }

  /**
   * Load generated scenario source and return a proxy for calling its methods.
   *
   * @param source - Generated JavaScript source code
   * @param family - The scenario family (used for validation)
   * @param name - The scenario name
   * @throws CodegenUnsupportedFamilyError if family is not supported
   */
  async loadScenario(
    source: string,
    family: ScenarioFamilyName,
    name: string,
  ): Promise<ScenarioProxy> {
    if (family === "operator_loop" || family === "game") {
      throw new CodegenUnsupportedFamilyError(family);
    }

    const requiredMethods = REQUIRED_METHODS[family];
    if (!requiredMethods) {
      throw new CodegenUnsupportedFamilyError(family);
    }

    // Validate the source exports the required methods by running a check
    const validationCode = `
${source}

const exportedScenario = module.exports.scenario || module.exports;
const missing = [];
${requiredMethods.map((m) => `if (typeof exportedScenario.${m} !== 'function') missing.push('${m}');`).join("\n")}
module.exports = { valid: missing.length === 0, missing };
`;

    const validationResult = await this.runtime.run<{ valid: boolean; missing: string[] }>(validationCode);
    if (validationResult.code !== 0) {
      throw new Error(
        `Generated scenario code failed to load: ${validationResult.errorMessage ?? `exit code ${validationResult.code}`}`,
      );
    }
    const validation = validationResult.exports;
    if (!validation?.valid) {
      throw new Error(
        `Generated scenario for '${name}' (family '${family}') is missing required methods: ${validation?.missing?.join(", ") ?? "unknown"}`,
      );
    }

    // Create the proxy that calls into the sandbox for each method invocation
    const runtime = this.runtime;
    const proxy: ScenarioProxy = {
      family,
      name,
      async call<T = unknown>(method: string, ...args: unknown[]): Promise<T> {
        const callCode = `
${source}
const exportedScenario = module.exports.scenario || module.exports;
const args = ${JSON.stringify(args)};
const result = exportedScenario.${method}(...args);
module.exports = { result };
`;
        const callResult = await runtime.run<{ result: T }>(callCode);
        if (callResult.code !== 0) {
          throw new Error(
            `Scenario method '${method}' failed: ${callResult.errorMessage ?? `exit code ${callResult.code}`}`,
          );
        }
        return callResult.exports?.result as T;
      },
      dispose() {
        // Runtime is shared — disposed by ScenarioRuntime.dispose()
      },
    };

    return proxy;
  }

  /**
   * Dispose the underlying V8 runtime.
   */
  dispose(): void {
    this.runtime.dispose();
  }
}
