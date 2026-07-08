// Opt-in RepairGate: run deterministic repairs and emit trace events (AC-878).
//
// TypeScript half of the AC-878 gate. It mirrors the Python reference at
// `autocontext/src/autocontext/harness_optimization/repair_gate.py`: the gate is
// a thin, deterministic orchestrator over the pure repairs in `./repairs.js`. It
// does NOT decide whether it is active; that is the caller's job via
// `repairGateActiveFor`. When the caller determines the gate is active, it
// constructs a `RepairGate` and calls `run`, which invokes each enabled repair
// over the handed context, emits exactly one `repair_applied` / `repair_skipped`
// event per repair, and returns the collected `RepairResult` list.
//
// The gate MAY apply the decision a pure repair returns (record the repaired
// tool-call json string, record the relocation target) by writing it back onto
// the context, but it never introduces or alters task content. `repair_applied`
// is emitted when a repair's status is `applied`; `repair_skipped` covers both
// `skipped` and `not_applicable`. Events go on the `repair` channel and carry
// the RepairResult verbatim, so every payload validates against the schema.

import type { EventStreamEmitter } from "../loop/events.js";
import type { RepairResult } from "./contract/generated-types.js";
import type { ArtifactContractProbeInputs } from "../control-plane/contract-probes/index.js";
import { finishGuard, repairArtifactLanding, repairToolCallJson } from "./repairs.js";

export const REPAIR_CHANNEL = "repair";

/** Config carrying the global flag plus the scenario allowlist. */
export interface RepairGateConfig {
  readonly enabled: boolean;
  readonly scenarios: string | readonly string[];
}

/**
 * True iff the gate is globally enabled AND the scenario is allowlisted.
 *
 * The allowlist is either a comma-separated string or a string array; an empty
 * allowlist means no scenario is active even when the global flag is on. This is
 * the sole opt-in decision: callers check it and only build and run a
 * `RepairGate` when it returns true.
 */
export function repairGateActiveFor(config: RepairGateConfig, scenarioName: string): boolean {
  const raw = typeof config.scenarios === "string" ? config.scenarios.split(",") : config.scenarios;
  const allowlist = raw.map((s) => s.trim()).filter((s) => s.length > 0);
  return config.enabled && allowlist.includes(scenarioName);
}

/**
 * Recorded state handed to the gate, one field group per enabled repair.
 *
 * Every field is optional: a repair whose input is absent returns a
 * `not_applicable` result. The gate writes applied decisions back onto the two
 * output fields (`repairedToolCallJson`, `relocationTarget`).
 */
export interface RepairContext {
  toolCallJson?: string;
  repairedToolCallJson?: string;
  artifactExpected?: ArtifactContractProbeInputs;
  artifactProduced?: Record<string, string>;
  relocationTarget?: string;
  finishClaimedDone?: boolean;
  finishCompletionOk?: boolean;
  finishReasonIfNot?: string;
}

export type RepairStep = (ctx: RepairContext) => RepairResult;

/** A `not_applicable` result for a repair whose input is absent. */
function absentResult(repairName: string, reason: string): RepairResult {
  return {
    schema_version: 1,
    repair_name: repairName,
    status: "not_applicable",
    reason,
    target: "",
    before: { present: false },
    after: { present: false },
    // These absent-input repairs (tool_call_json, artifact_landing, finish_guard) are implemented in
    // BOTH languages, so parity is implemented/implemented, matching their applied and not_applicable
    // results. Stamping python "pending" made a normal skipped event look like a parity gap.
    parity: { python: "implemented", typescript: "implemented", schema_hash: "" },
  };
}

/** Run the tool-call json repair; record the repaired string when applied. */
export function repairToolCallJsonStep(ctx: RepairContext): RepairResult {
  if (ctx.toolCallJson === undefined) {
    return absentResult("tool_call_json", "no tool-call json in context");
  }
  const { value, result } = repairToolCallJson(ctx.toolCallJson);
  if (result.status === "applied" && value !== null) {
    ctx.repairedToolCallJson = value;
  }
  return result;
}

/** Run the artifact-landing repair; record the relocation target when applied. */
export function repairArtifactLandingStep(ctx: RepairContext): RepairResult {
  if (ctx.artifactExpected === undefined) {
    return absentResult("artifact_landing", "no expected artifact contract in context");
  }
  const { path, result } = repairArtifactLanding({
    expected: ctx.artifactExpected,
    produced: ctx.artifactProduced ?? {},
  });
  if (result.status === "applied" && path !== null) {
    ctx.relocationTarget = path;
  }
  return result;
}

/** Run the finish guard when a completion claim is present in the context. */
export function finishGuardStep(ctx: RepairContext): RepairResult {
  if (ctx.finishClaimedDone === undefined) {
    return absentResult("finish_guard", "no finish claim in context");
  }
  return finishGuard({
    claimedDone: ctx.finishClaimedDone,
    completionOk: ctx.finishCompletionOk ?? true,
    reasonIfNot: ctx.finishReasonIfNot ?? "",
  });
}

// loop_guard is intentionally omitted from the default set: it has no TypeScript
// mirror yet, and the default set is kept identical across languages.
export const DEFAULT_REPAIRS: readonly RepairStep[] = [
  repairToolCallJsonStep,
  repairArtifactLandingStep,
  finishGuardStep,
];

/**
 * Thin orchestrator: run each enabled repair, emit one event per result.
 *
 * `run` does NOT check whether the gate is active; the caller gates via
 * `repairGateActiveFor` and only constructs and runs the gate when active.
 */
export class RepairGate {
  readonly #emitter: EventStreamEmitter;
  readonly #repairs: readonly RepairStep[];
  readonly #channel: string;

  constructor(
    emitter: EventStreamEmitter,
    repairs: readonly RepairStep[] = DEFAULT_REPAIRS,
    channel: string = REPAIR_CHANNEL,
  ) {
    this.#emitter = emitter;
    this.#repairs = repairs;
    this.#channel = channel;
  }

  /**
   * Invoke each enabled repair over `context`, emitting an event each.
   *
   * The emitted payload is `{ scenario, result }`: the RepairResult stays a
   * self-contained, schema-valid object under `result`, and the scenario rides
   * alongside as a sibling so consumers can attribute the repair without
   * polluting the RepairResult schema. Mirrors the Python `RepairGate.run`.
   */
  run(scenarioName: string, context: RepairContext): RepairResult[] {
    const results: RepairResult[] = [];
    for (const repair of this.#repairs) {
      const result = repair(context);
      const event = result.status === "applied" ? "repair_applied" : "repair_skipped";
      const payload = { scenario: scenarioName, result } as unknown as Record<string, unknown>;
      this.#emitter.emit(event, payload, this.#channel);
      results.push(result);
    }
    return results;
  }
}
