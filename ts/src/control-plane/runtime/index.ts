// Public surface of the autocontext control-plane runtime helpers.
//
// v1 exposes only `chooseModel` (AC-545, spec §4). Import discipline: this
// module sits in runtime/ and depends on contract/ + actuators/model-routing/
// for config types. It does NOT import from emit/, registry/, promotion/, or
// production-traces/. Callers that want to record a routing decision on a
// ProductionTrace do so themselves — the router does not touch I/O.

export { chooseModel } from "./model-router.js";
export type {
  ChooseModelInputs,
  ModelDecision,
  ModelDecisionReason,
  ModelRouterContext,
} from "./model-router.js";
