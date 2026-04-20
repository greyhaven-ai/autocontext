// chooseModel — pure runtime helper that consults a ModelRoutingPayload
// (validated config) and returns a ModelDecision. Spec §4 (AC-545).
//
// Pure: no I/O, no clock, no random. `evaluatedAt` is injected as `nowIso` so
// the output is reproducible in tests and audit logs.
//
// Import discipline: runtime/ imports from contract/ + actuators/model-routing/
// (for the config types). It does NOT import from emit/, registry/, or
// production-traces/. Trace emission is the caller's responsibility.
//
// DDD vocabulary (from spec §4, verbatim): `default`, `routes`, `fallback`,
// `match`, `rollout`, `budget`, `latency`, `confidence`, `cohortKey`.

import { createHash } from "node:crypto";
import type {
  FallbackEntry,
  FallbackReason,
  MatchExpression,
  MatchOperator,
  ModelRoutingPayload,
  Route,
} from "../actuators/model-routing/schema.js";

// ---- Types ----

/**
 * Context inputs to the router. Field names mirror the spec's dotted path
 * vocabulary — `env.taskType`, `session.sessionIdHash` — flattened into a
 * single object for ergonomic call sites. The router maps dotted paths to
 * these flat fields internally (see `lookupContextValue`).
 */
export interface ModelRouterContext {
  readonly taskType?: string;
  readonly tenant?: string;
  readonly budgetRemainingUsd?: number;
  readonly latencyBudgetMs?: number;
  readonly sessionIdHash?: string;
  readonly confidenceScore?: number;
  readonly previousFailure?: "provider-error" | "latency-breached" | "budget-exceeded";
}

export interface ChooseModelInputs {
  readonly config: ModelRoutingPayload;
  readonly context: ModelRouterContext;
}

export type ModelDecisionReason = "default" | "matched-route" | "fallback";

export interface ModelDecision {
  readonly chosen: {
    readonly provider: string;
    readonly model: string;
    readonly endpoint?: string;
  };
  readonly reason: ModelDecisionReason;
  readonly matchedRouteId?: string;
  readonly fallbackReason?: FallbackReason;
  readonly evaluatedAt: string;
}

// ---- Helpers ----

/**
 * Map a dotted path (e.g. "env.taskType" or "session.sessionIdHash") to the
 * corresponding field in the flat context. v1 supports a closed set of paths
 * — unknown paths return `undefined` and the operator is considered non-
 * matching. (This keeps semantics conservative and the surface small.)
 */
function lookupContextValue(path: string, ctx: ModelRouterContext): unknown {
  switch (path) {
    case "env.taskType":
      return ctx.taskType;
    case "env.tenant":
      return ctx.tenant;
    case "session.sessionIdHash":
      return ctx.sessionIdHash;
    default:
      return undefined;
  }
}

/**
 * Decide whether a per-field operator object matches the context value. The
 * operator object may set exactly one of { equals, contains, default:true }.
 * `default: true` matches any context (including undefined). Other operators
 * require a defined context value.
 */
function operatorMatches(op: MatchOperator, value: unknown): boolean {
  const operatorCount = [
    op.default === true,
    op.equals !== undefined,
    op.contains !== undefined,
  ].filter(Boolean).length;
  if (operatorCount !== 1) return false;

  if (op.default === true) return true;
  if (op.equals !== undefined) {
    return value === op.equals;
  }
  if (op.contains !== undefined) {
    if (typeof value !== "string") return false;
    if (typeof op.contains === "string") {
      return value.includes(op.contains);
    }
    // Array form: any element a string the value contains.
    for (const needle of op.contains) {
      if (typeof needle === "string" && value.includes(needle)) return true;
    }
    return false;
  }
  // No operator set — treat as non-matching (conservative).
  return false;
}

/** All per-field operators in a MatchExpression must match (AND semantics). */
function matchExpressionMatches(match: MatchExpression, ctx: ModelRouterContext): boolean {
  const entries = Object.entries(match);
  if (entries.length === 0) return false;
  for (const [path, op] of entries) {
    const value = lookupContextValue(path, ctx);
    if (!operatorMatches(op, value)) return false;
  }
  return true;
}

/**
 * Rollout bucket check: `hash(cohortValue) mod 100 < percent`. The cohortKey
 * is a dotted path into the context. Missing cohort value ⇒ route does not
 * match (conservative — don't bucket unknown traffic).
 */
function rolloutMatches(
  rollout: NonNullable<Route["rollout"]>,
  ctx: ModelRouterContext,
): boolean {
  const cohortValue = lookupContextValue(rollout.cohortKey, ctx);
  if (typeof cohortValue !== "string" || cohortValue.length === 0) {
    return false;
  }
  if (rollout.percent >= 100) return true;
  if (rollout.percent <= 0) return false;
  const digest = createHash("sha256").update(cohortValue).digest();
  const bucket = digest.readUInt32BE(0) % 100;
  return bucket < rollout.percent;
}

/**
 * Confidence guardrail: if the route declares a minScore, the context must
 * provide a confidenceScore ≥ minScore for the route to be considered
 * matching. Missing confidenceScore → skip (conservative).
 */
function confidenceMatches(route: Route, ctx: ModelRouterContext): boolean {
  const conf = route.confidence;
  if (conf === undefined) return true;
  if (typeof ctx.confidenceScore !== "number") return false;
  return ctx.confidenceScore >= conf.minScore;
}

/**
 * Guardrail demotion: if the route matches but a budget/latency guardrail is
 * violated, return the appropriate fallback reason. `undefined` means no
 * demotion.
 */
function guardrailDemotion(
  route: Route,
  ctx: ModelRouterContext,
): FallbackReason | undefined {
  if (route.budget !== undefined) {
    const remaining = ctx.budgetRemainingUsd;
    if (typeof remaining === "number" && remaining < route.budget.maxCostUsdPerCall) {
      return "budget-exceeded";
    }
  }
  if (route.latency !== undefined) {
    const budget = ctx.latencyBudgetMs;
    if (typeof budget === "number" && budget < route.latency.maxP95Ms) {
      return "latency-breached";
    }
  }
  return undefined;
}

/** Map a `previousFailure` context value to the corresponding FallbackReason. */
function previousFailureReason(ctx: ModelRouterContext): FallbackReason | undefined {
  switch (ctx.previousFailure) {
    case "provider-error":
      return "provider-error";
    case "latency-breached":
      return "latency-breached";
    case "budget-exceeded":
      return "budget-exceeded";
    default:
      return undefined;
  }
}

/**
 * Pick the first fallback whose `when` filter includes `reason` (or omits the
 * filter entirely — an unconditional fallback). Returns undefined if the
 * chain is exhausted.
 */
function pickFallback(
  fallback: readonly FallbackEntry[],
  reason: FallbackReason,
): FallbackEntry | undefined {
  for (const entry of fallback) {
    if (entry.when === undefined || entry.when.length === 0) return entry;
    if (entry.when.includes(reason)) return entry;
  }
  return undefined;
}

function toChosen(target: {
  readonly provider: string;
  readonly model: string;
  readonly endpoint?: string | null;
}): ModelDecision["chosen"] {
  return target.endpoint !== undefined && target.endpoint !== null
    ? { provider: target.provider, model: target.model, endpoint: target.endpoint }
    : { provider: target.provider, model: target.model };
}

// ---- chooseModel ----

/**
 * Decide which model to use given a config, context, and a nowIso. Pure and
 * deterministic: given the same inputs and the same nowIso, returns the same
 * ModelDecision.
 *
 * Algorithm (spec §4):
 *   1. Walk `config.routes` in declared order. For each route:
 *      - check match expression (AND of per-field operators)
 *      - check confidence guardrail (skip if below minScore)
 *      - check rollout bucket (skip if cohort value missing or bucket ≥ percent)
 *      If all pass, the route is a candidate.
 *   2. If a candidate route is found:
 *      - if `context.previousFailure` is set → demote to fallback with that reason
 *      - else if a budget/latency guardrail is violated → demote to fallback
 *      - else → return the route's target with reason=matched-route
 *   3. If no route matches → return `config.default` with reason=default.
 *
 * Fallback resolution: walk `config.fallback` in order; first entry whose
 * `when` filter includes the reason (or has no filter) wins. If the list is
 * exhausted, fall back to `config.default` but keep the reason=fallback so
 * audit logs reflect the demotion.
 */
export function chooseModel(inputs: ChooseModelInputs, nowIso: string): ModelDecision {
  const { config, context } = inputs;

  for (const route of config.routes) {
    if (!matchExpressionMatches(route.match, context)) continue;
    if (!confidenceMatches(route, context)) continue;
    if (route.rollout !== undefined && !rolloutMatches(route.rollout, context)) continue;

    // Route matched. Check for previousFailure short-circuit, then guardrails.
    const prev = previousFailureReason(context);
    if (prev !== undefined) {
      return buildFallback(config, prev, route.id, nowIso);
    }
    const demotion = guardrailDemotion(route, context);
    if (demotion !== undefined) {
      return buildFallback(config, demotion, route.id, nowIso);
    }

    return {
      chosen: toChosen(route.target),
      reason: "matched-route",
      matchedRouteId: route.id,
      evaluatedAt: nowIso,
    };
  }

  // No route matched → default path. previousFailure without a matched route
  // does not trigger a fallback (there's nothing to fall back *from*).
  return {
    chosen: toChosen(config.default),
    reason: "default",
    evaluatedAt: nowIso,
  };
}

function buildFallback(
  config: ModelRoutingPayload,
  reason: FallbackReason,
  matchedRouteId: string,
  nowIso: string,
): ModelDecision {
  const picked = pickFallback(config.fallback, reason);
  const target = picked ?? config.default;
  return {
    chosen: toChosen(target),
    reason: "fallback",
    matchedRouteId,
    fallbackReason: reason,
    evaluatedAt: nowIso,
  };
}
