import { describe, test, expect } from "vitest";
import {
  clusterByTaskType,
  clusterByRules,
  matchExpression,
  resolveJsonPath,
  UNCATEGORIZED_CLUSTER,
} from "../../../../src/production-traces/dataset/cluster.js";
import { makeTrace } from "./_helpers/fixtures.js";
import type { ClusterConfig } from "../../../../src/production-traces/dataset/types.js";

describe("clusterByTaskType (Tier 1)", () => {
  test("groups by env.taskType", () => {
    const traces = [
      makeTrace({ traceId: "01K0000000000000000000000A", taskType: "checkout" }),
      makeTrace({ traceId: "01K0000000000000000000000B", taskType: "password" }),
      makeTrace({ traceId: "01K0000000000000000000000C", taskType: "checkout" }),
    ];
    const out = clusterByTaskType(traces);
    expect(out.get("checkout")?.length).toBe(2);
    expect(out.get("password")?.length).toBe(1);
    expect(out.has(UNCATEGORIZED_CLUSTER)).toBe(false);
  });

  test("puts traces without taskType into `uncategorized`", () => {
    const traces = [
      makeTrace({ traceId: "01K0000000000000000000000A", taskType: "checkout" }),
      makeTrace({ traceId: "01K0000000000000000000000B" }),
      makeTrace({ traceId: "01K0000000000000000000000C" }),
    ];
    const out = clusterByTaskType(traces);
    expect(out.get("checkout")?.length).toBe(1);
    expect(out.get(UNCATEGORIZED_CLUSTER)?.length).toBe(2);
  });

  test("preserves input order within each cluster", () => {
    const a = makeTrace({ traceId: "01K0000000000000000000000A", taskType: "x" });
    const b = makeTrace({ traceId: "01K0000000000000000000000B", taskType: "x" });
    const c = makeTrace({ traceId: "01K0000000000000000000000C", taskType: "x" });
    const out = clusterByTaskType([a, b, c]);
    expect(out.get("x")?.map((t) => t.traceId)).toEqual([a.traceId, b.traceId, c.traceId]);
  });

  test("handles empty input", () => {
    expect(clusterByTaskType([]).size).toBe(0);
  });
});

describe("resolveJsonPath", () => {
  const trace = makeTrace({
    messages: [
      { role: "user", content: "please check my cart", timestamp: "2026-04-17T12:00:00.000Z" },
    ],
    toolCalls: [
      { toolName: "reset_password", args: {} },
    ],
  });

  test("dotted keys", () => {
    expect(resolveJsonPath(trace, "env.environmentTag")).toBe("production");
  });

  test("bracketed integer index", () => {
    expect(resolveJsonPath(trace, "messages[0].content")).toBe("please check my cart");
    expect(resolveJsonPath(trace, "toolCalls[0].toolName")).toBe("reset_password");
  });

  test("missing keys return undefined", () => {
    expect(resolveJsonPath(trace, "nope.absent")).toBe(undefined);
    expect(resolveJsonPath(trace, "messages[99].content")).toBe(undefined);
  });
});

describe("matchExpression", () => {
  const trace = makeTrace({
    messages: [
      { role: "user", content: "please check my cart", timestamp: "2026-04-17T12:00:00.000Z" },
    ],
    toolCalls: [
      { toolName: "reset_password", args: {} },
    ],
    taskType: "checkout",
  });

  test("equals matches exact value", () => {
    expect(matchExpression(trace, { "env.taskType": { equals: "checkout" } })).toBe(true);
    expect(matchExpression(trace, { "env.taskType": { equals: "other" } })).toBe(false);
  });

  test("contains matches substring on strings", () => {
    expect(matchExpression(trace, { "messages[0].content": { contains: "cart" } })).toBe(true);
    expect(matchExpression(trace, { "messages[0].content": { contains: "nothing" } })).toBe(false);
  });

  test("contains with array is ANY-match", () => {
    expect(matchExpression(trace, { "messages[0].content": { contains: ["cart", "zz"] } })).toBe(true);
    expect(matchExpression(trace, { "messages[0].content": { contains: ["zz", "yy"] } })).toBe(false);
  });

  test("default operator always matches", () => {
    expect(matchExpression(trace, { anyPath: { default: true } })).toBe(true);
  });

  test("empty expression never matches", () => {
    expect(matchExpression(trace, {})).toBe(false);
  });

  test("AND semantics: multiple path/operator pairs must all match", () => {
    expect(matchExpression(trace, {
      "env.taskType": { equals: "checkout" },
      "messages[0].content": { contains: "cart" },
    })).toBe(true);
    expect(matchExpression(trace, {
      "env.taskType": { equals: "checkout" },
      "messages[0].content": { contains: "zz" },
    })).toBe(false);
  });
});

describe("clusterByRules (Tier 2)", () => {
  const traceCart = makeTrace({
    traceId: "01K0000000000000000000000A",
    messages: [
      { role: "user", content: "please checkout my cart", timestamp: "2026-04-17T12:00:00.000Z" },
    ],
  });
  const tracePassword = makeTrace({
    traceId: "01K0000000000000000000000B",
    toolCalls: [{ toolName: "reset_password", args: {} }],
  });
  const traceOther = makeTrace({ traceId: "01K0000000000000000000000C" });

  const config: ClusterConfig = {
    strategy: "rules",
    rules: [
      { id: "checkout", match: { "messages[0].content": { contains: ["checkout", "cart"] } } },
      { id: "password-reset", match: { "toolCalls[0].toolName": { equals: "reset_password" } } },
      { id: "uncategorized", match: { default: { default: true } } },
    ],
  };

  test("first-matching-rule wins", () => {
    const out = clusterByRules([traceCart, tracePassword, traceOther], config);
    expect(out.get("checkout")?.map((t) => t.traceId)).toEqual([traceCart.traceId]);
    expect(out.get("password-reset")?.map((t) => t.traceId)).toEqual([tracePassword.traceId]);
    expect(out.get("uncategorized")?.map((t) => t.traceId)).toEqual([traceOther.traceId]);
  });

  test("catch-all via default: true", () => {
    const out = clusterByRules([traceOther], config);
    expect(out.get("uncategorized")?.length).toBe(1);
  });

  test("no catch-all → trace with no rule match goes to UNCATEGORIZED_CLUSTER", () => {
    const narrow: ClusterConfig = {
      strategy: "rules",
      rules: [
        { id: "only-checkout", match: { "messages[0].content": { contains: "cart" } } },
      ],
    };
    const out = clusterByRules([traceOther], narrow);
    expect(out.get(UNCATEGORIZED_CLUSTER)?.length).toBe(1);
    expect(out.has("only-checkout")).toBe(false);
  });

  test("preserves input order within each cluster", () => {
    const t1 = makeTrace({
      traceId: "01K0000000000000000000000D",
      messages: [{ role: "user", content: "cart", timestamp: "2026-04-17T12:00:00.000Z" }],
    });
    const t2 = makeTrace({
      traceId: "01K0000000000000000000000E",
      messages: [{ role: "user", content: "cart", timestamp: "2026-04-17T12:00:01.000Z" }],
    });
    const out = clusterByRules([t1, t2], config);
    expect(out.get("checkout")?.map((t) => t.traceId)).toEqual([t1.traceId, t2.traceId]);
  });
});
