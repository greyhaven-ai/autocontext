// Integration test: build a ProductionTrace with the new `routing` field set
// from a ModelDecision. Validate via AJV and (when uv is available) via the
// Python Pydantic validator — cross-runtime parity check for the additive
// routing field (spec §4, AC-545).

import { describe, test, expect } from "vitest";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import { chooseModel } from "../../../src/control-plane/runtime/model-router.js";
import { createProductionTrace } from "../../../src/production-traces/contract/factories.js";
import { validateProductionTrace } from "../../../src/production-traces/contract/validators.js";
import type {
  AppId,
  EnvironmentTag,
} from "../../../src/production-traces/contract/branded-ids.js";
import type {
  ModelRoutingDecisionReason,
  ModelRoutingFallbackReason,
  ProductionTraceRouting,
} from "../../../src/production-traces/contract/types.js";
import type { ModelRoutingPayload } from "../../../src/control-plane/actuators/model-routing/schema.js";

const NOW = "2026-04-17T12:00:00.000Z";
const TS_ROOT = resolve(__dirname, "..", "..", "..");
const WORKTREE_ROOT = resolve(TS_ROOT, "..");
const PY_CWD = resolve(WORKTREE_ROOT, "autocontext");

type PythonResult = { valid: boolean; error?: string };

function runPythonValidate(input: unknown): PythonResult {
  const script = [
    "import json, sys",
    "from pydantic import ValidationError",
    "from autocontext.production_traces import validate_production_trace",
    "data = json.loads(sys.stdin.read())",
    "try:",
    "    trace = validate_production_trace(data)",
    "    out = {'valid': True}",
    "    print(json.dumps(out))",
    "except ValidationError as e:",
    "    print(json.dumps({'valid': False, 'error': str(e)}))",
  ].join("\n");
  const result = spawnSync("uv", ["run", "python", "-c", script], {
    cwd: PY_CWD,
    input: JSON.stringify(input),
    encoding: "utf-8",
    env: process.env,
  });
  if (result.status !== 0 && !result.stdout) {
    throw new Error(`python validate exited ${result.status}: ${result.stderr}`);
  }
  const line = result.stdout.trim().split("\n").pop() ?? "{}";
  return JSON.parse(line) as PythonResult;
}

function hasUv(): boolean {
  const r = spawnSync("uv", ["--version"], { encoding: "utf-8" });
  return r.status === 0;
}

const UV_AVAILABLE = hasUv();

const ROUTING_CONFIG: ModelRoutingPayload = {
  schemaVersion: "1.0",
  default: { provider: "anthropic", model: "claude-sonnet-4-5", endpoint: null },
  routes: [
    {
      id: "checkout-specialized",
      match: { "env.taskType": { equals: "checkout" } },
      target: {
        provider: "openai-compatible",
        model: "finetuned-checkout-v3",
        endpoint: "https://my-vllm/v1",
      },
    },
  ],
  fallback: [{ provider: "anthropic", model: "claude-haiku-4-5" }],
};

function decisionToRoutingField(decision: {
  chosen: { provider: string; model: string; endpoint?: string };
  reason: ModelRoutingDecisionReason;
  matchedRouteId?: string;
  fallbackReason?: ModelRoutingFallbackReason;
  evaluatedAt: string;
}): ProductionTraceRouting {
  return {
    chosen: decision.chosen,
    reason: decision.reason,
    ...(decision.matchedRouteId !== undefined
      ? { matchedRouteId: decision.matchedRouteId }
      : {}),
    ...(decision.fallbackReason !== undefined
      ? { fallbackReason: decision.fallbackReason }
      : {}),
    evaluatedAt: decision.evaluatedAt,
  };
}

function baseTrace(routing?: ProductionTraceRouting) {
  return createProductionTrace({
    source: { emitter: "sdk", sdk: { name: "ts", version: "0.4.3" } },
    provider: { name: "anthropic" },
    model: "claude-sonnet-4-5",
    env: {
      environmentTag: "production" as EnvironmentTag,
      appId: "my-app" as AppId,
    },
    messages: [{ role: "user", content: "x", timestamp: NOW }],
    timing: {
      startedAt: NOW,
      endedAt: "2026-04-17T12:00:01.000Z",
      latencyMs: 1000,
    },
    usage: { tokensIn: 10, tokensOut: 5 },
    ...(routing !== undefined ? { routing } : {}),
  });
}

describe("ProductionTrace with routing field — TS AJV validation", () => {
  test("trace without routing field validates (additive-optional, backward compatible)", () => {
    const trace = baseTrace();
    expect(validateProductionTrace(trace).valid).toBe(true);
    expect(trace.routing).toBeUndefined();
  });

  test("trace with default-path routing decision validates", () => {
    const decision = chooseModel({ config: ROUTING_CONFIG, context: {} }, NOW);
    const trace = baseTrace(decisionToRoutingField(decision));
    expect(validateProductionTrace(trace).valid).toBe(true);
    expect(trace.routing?.reason).toBe("default");
    expect(trace.routing?.chosen.model).toBe("claude-sonnet-4-5");
    expect(trace.routing?.evaluatedAt).toBe(NOW);
  });

  test("trace with matched-route routing decision validates", () => {
    const decision = chooseModel(
      { config: ROUTING_CONFIG, context: { taskType: "checkout" } },
      NOW,
    );
    const trace = baseTrace(decisionToRoutingField(decision));
    expect(validateProductionTrace(trace).valid).toBe(true);
    expect(trace.routing?.reason).toBe("matched-route");
    expect(trace.routing?.matchedRouteId).toBe("checkout-specialized");
    expect(trace.routing?.chosen.endpoint).toBe("https://my-vllm/v1");
  });

  test("AJV rejects routing field with unknown reason", () => {
    const trace = baseTrace({
      chosen: { provider: "x", model: "y" },
      reason: "bogus-reason" as ModelRoutingDecisionReason,
      evaluatedAt: NOW,
    });
    expect(validateProductionTrace(trace).valid).toBe(false);
  });

  test("AJV rejects routing.chosen missing required provider", () => {
    const trace = {
      ...baseTrace(),
      routing: {
        chosen: { model: "y" },
        reason: "default" as const,
        evaluatedAt: NOW,
      },
    };
    expect(validateProductionTrace(trace).valid).toBe(false);
  });

  test("AJV rejects additional properties on routing (strict)", () => {
    const trace = {
      ...baseTrace(),
      routing: {
        chosen: { provider: "x", model: "y" },
        reason: "default" as const,
        evaluatedAt: NOW,
        extra: "nope",
      },
    };
    expect(validateProductionTrace(trace).valid).toBe(false);
  });
});

const maybeDescribe = UV_AVAILABLE ? describe : describe.skip;

maybeDescribe("ProductionTrace with routing field — cross-runtime (TS AJV vs Python Pydantic)", () => {
  test("matched-route decision: accepted by both runtimes", () => {
    const decision = chooseModel(
      { config: ROUTING_CONFIG, context: { taskType: "checkout" } },
      NOW,
    );
    const trace = baseTrace(decisionToRoutingField(decision));
    const ts = validateProductionTrace(trace).valid;
    const py = runPythonValidate(trace).valid;
    expect(ts).toBe(true);
    expect(py).toBe(true);
  }, 30_000);

  test("fallback decision with fallbackReason: accepted by both runtimes", () => {
    const fallbackConfig: ModelRoutingPayload = {
      ...ROUTING_CONFIG,
      routes: [
        {
          id: "budget-bound",
          match: { "env.taskType": { equals: "checkout" } },
          target: { provider: "openai-compatible", model: "expensive" },
          budget: { maxCostUsdPerCall: 0.02 },
        },
      ],
    };
    const decision = chooseModel(
      {
        config: fallbackConfig,
        context: { taskType: "checkout", budgetRemainingUsd: 0.001 },
      },
      NOW,
    );
    expect(decision.reason).toBe("fallback");
    expect(decision.fallbackReason).toBe("budget-exceeded");
    const trace = baseTrace(decisionToRoutingField(decision));
    expect(validateProductionTrace(trace).valid).toBe(true);
    expect(runPythonValidate(trace).valid).toBe(true);
  }, 30_000);

  test("trace without routing field (backward compat) accepted by both runtimes", () => {
    const trace = baseTrace();
    expect(validateProductionTrace(trace).valid).toBe(true);
    expect(runPythonValidate(trace).valid).toBe(true);
  }, 30_000);

  test("invalid routing.reason rejected by both runtimes", () => {
    const trace = {
      ...baseTrace(),
      routing: {
        chosen: { provider: "x", model: "y" },
        reason: "not-a-reason",
        evaluatedAt: NOW,
      },
    };
    expect(validateProductionTrace(trace).valid).toBe(false);
    expect(runPythonValidate(trace).valid).toBe(false);
  }, 30_000);
});
