/**
 * Loop module — generation loop components.
 */

export { HypothesisTree, HypothesisNodeSchema } from "./hypothesis-tree.js";
export type { HypothesisNode } from "./hypothesis-tree.js";

export { EventStreamEmitter } from "./events.js";
export type { EventCallback } from "./events.js";

export { LoopController } from "./controller.js";

export { BackpressureGate, TrendAwareGate } from "./backpressure.js";
export type { GateDecision, ScoreHistory, TrendAwareGateOpts } from "./backpressure.js";

export { GenerationRunner } from "./generation-runner.js";
export type { GenerationRunnerOpts, RunResult } from "./generation-runner.js";
