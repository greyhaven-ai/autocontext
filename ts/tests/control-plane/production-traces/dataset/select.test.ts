import { describe, test, expect } from "vitest";
import {
  applySelectionRules,
  applySelectionRulesPerCluster,
  extractSplitRule,
  rulesWithoutSplit,
} from "../../../../src/production-traces/dataset/select.js";
import { makeTrace } from "./_helpers/fixtures.js";
import type {
  GateRule,
  TopQuartileRule,
  ContrastiveRule,
  SplitRule,
  SelectionRule,
} from "../../../../src/production-traces/dataset/types.js";
import type { ProductionTrace } from "../../../../src/production-traces/contract/types.js";

describe("gate rule", () => {
  const t1 = makeTrace({ traceId: "01K0000000000000000000000A", taskType: "checkout" });
  const t2 = makeTrace({ traceId: "01K0000000000000000000000B", taskType: "password" });
  const t3 = makeTrace({ traceId: "01K0000000000000000000000C", taskType: "checkout" });

  test("include[] requires all entries to match (AND)", () => {
    const rule: GateRule = {
      type: "gate",
      include: [{ "env.taskType": { equals: "checkout" } }],
    };
    const { rows } = applySelectionRules([t1, t2, t3], [rule], 0);
    expect(rows.map((t) => t.traceId)).toEqual([t1.traceId, t3.traceId]);
  });

  test("exclude[] removes matching traces (OR)", () => {
    const rule: GateRule = {
      type: "gate",
      exclude: [{ "env.taskType": { equals: "password" } }],
    };
    const { rows } = applySelectionRules([t1, t2, t3], [rule], 0);
    expect(rows.map((t) => t.traceId)).toEqual([t1.traceId, t3.traceId]);
  });

  test("empty gate passes everything through", () => {
    const rule: GateRule = { type: "gate" };
    const { rows } = applySelectionRules([t1, t2, t3], [rule], 0);
    expect(rows.length).toBe(3);
  });
});

describe("top-quartile rule", () => {
  const mkScored = (id: string, score: number): ProductionTrace =>
    makeTrace({
      traceId: id,
      outcome: { score },
    });

  test("keeps top 25% by outcome.score (percentile: 75)", () => {
    const traces = [
      mkScored("01K00000000000000000000001", 0.9),
      mkScored("01K00000000000000000000002", 0.5),
      mkScored("01K00000000000000000000003", 0.7),
      mkScored("01K00000000000000000000004", 0.3),
    ];
    const rule: TopQuartileRule = { type: "top-quartile", by: "outcome.score", percentile: 75 };
    const { rows } = applySelectionRules(traces, [rule], 0);
    // Top 25% of 4 = 1 item, the highest score 0.9.
    expect(rows.length).toBe(1);
    expect(rows[0].traceId).toBe(traces[0].traceId);
  });

  test("excludes traces missing the score field", () => {
    const rule: TopQuartileRule = { type: "top-quartile", by: "outcome.score", percentile: 75 };
    const scored = mkScored("01K00000000000000000000001", 0.9);
    const unscored = makeTrace({ traceId: "01K00000000000000000000002" });
    const { rows } = applySelectionRules([scored, unscored], [rule], 0);
    expect(rows.length).toBe(1);
    expect(rows[0].traceId).toBe(scored.traceId);
  });

  test("empty input → empty output", () => {
    const rule: TopQuartileRule = { type: "top-quartile", by: "outcome.score", percentile: 50 };
    const { rows } = applySelectionRules([], [rule], 0);
    expect(rows.length).toBe(0);
  });
});

describe("contrastive rule", () => {
  const mkLabeled = (id: string, label: "success" | "failure" | "partial", taskType: string): ProductionTrace =>
    makeTrace({ traceId: id, taskType, outcome: { label } });

  test("pairs failures with successes within the same cluster", () => {
    const f1 = mkLabeled("01K00000000000000000000001", "failure", "checkout");
    const s1 = mkLabeled("01K00000000000000000000002", "success", "checkout");
    const f2 = mkLabeled("01K00000000000000000000003", "failure", "password");
    const s2 = mkLabeled("01K00000000000000000000004", "success", "password");
    const rule: ContrastiveRule = {
      type: "contrastive",
      failureCriterion: { "outcome.label": { equals: "failure" } },
      successCriterion: { "outcome.label": { equals: "success" } },
    };
    const result = applySelectionRules([f1, s1, f2, s2], [rule], 0);
    expect(result.pairs?.length).toBe(2);
    expect(result.rows.length).toBe(4);
  });

  test("maxPairsPerCluster caps pair count", () => {
    const traces = [
      mkLabeled("01K00000000000000000000001", "failure", "x"),
      mkLabeled("01K00000000000000000000002", "failure", "x"),
      mkLabeled("01K00000000000000000000003", "failure", "x"),
      mkLabeled("01K00000000000000000000004", "success", "x"),
      mkLabeled("01K00000000000000000000005", "success", "x"),
      mkLabeled("01K00000000000000000000006", "success", "x"),
    ];
    const rule: ContrastiveRule = {
      type: "contrastive",
      failureCriterion: { "outcome.label": { equals: "failure" } },
      successCriterion: { "outcome.label": { equals: "success" } },
      maxPairsPerCluster: 2,
    };
    const result = applySelectionRules(traces, [rule], 0);
    expect(result.pairs?.length).toBe(2);
  });

  test("no success partner → no pairs for that cluster", () => {
    const traces = [
      mkLabeled("01K00000000000000000000001", "failure", "x"),
      mkLabeled("01K00000000000000000000002", "failure", "x"),
    ];
    const rule: ContrastiveRule = {
      type: "contrastive",
      failureCriterion: { "outcome.label": { equals: "failure" } },
      successCriterion: { "outcome.label": { equals: "success" } },
    };
    const result = applySelectionRules(traces, [rule], 0);
    expect(result.pairs?.length).toBe(0);
    expect(result.rows.length).toBe(0);
  });
});

describe("composition", () => {
  test("rules apply in order: gate then contrastive", () => {
    const t1 = makeTrace({
      traceId: "01K00000000000000000000001",
      taskType: "checkout",
      outcome: { label: "failure" },
    });
    const t2 = makeTrace({
      traceId: "01K00000000000000000000002",
      taskType: "checkout",
      outcome: { label: "success" },
    });
    const t3 = makeTrace({
      traceId: "01K00000000000000000000003",
      taskType: "other",
      outcome: { label: "failure" },
    });
    const rules: SelectionRule[] = [
      { type: "gate", include: [{ "env.taskType": { equals: "checkout" } }] },
      {
        type: "contrastive",
        failureCriterion: { "outcome.label": { equals: "failure" } },
        successCriterion: { "outcome.label": { equals: "success" } },
      },
    ];
    const result = applySelectionRules([t1, t2, t3], rules, 0);
    expect(result.rows.length).toBe(2);
    expect(result.pairs?.length).toBe(1);
  });

  test("applySelectionRulesPerCluster processes each cluster independently", () => {
    const a1 = makeTrace({ traceId: "01K00000000000000000000001", taskType: "x", outcome: { score: 0.9 } });
    const a2 = makeTrace({ traceId: "01K00000000000000000000002", taskType: "x", outcome: { score: 0.1 } });
    const b1 = makeTrace({ traceId: "01K00000000000000000000003", taskType: "y", outcome: { score: 0.8 } });
    const rules: SelectionRule[] = [
      { type: "top-quartile", by: "outcome.score", percentile: 50, perCluster: true },
    ];
    const clusters = new Map<string, readonly ProductionTrace[]>([
      ["x", [a1, a2]],
      ["y", [b1]],
    ]);
    const out = applySelectionRulesPerCluster(clusters, rules, 0);
    expect(out.get("x")?.rows.length).toBe(1);
    expect(out.get("x")?.rows[0].traceId).toBe(a1.traceId);
    expect(out.get("y")?.rows.length).toBe(1);
  });
});

describe("split rule extraction", () => {
  test("extractSplitRule returns the last split rule", () => {
    const s1: SplitRule = { type: "split", train: 0.7, eval: 0.2, holdout: 0.1 };
    const rules: SelectionRule[] = [
      { type: "gate" },
      s1,
    ];
    expect(extractSplitRule(rules)).toBe(s1);
  });

  test("extractSplitRule returns null when absent", () => {
    expect(extractSplitRule([{ type: "gate" }])).toBeNull();
  });

  test("rulesWithoutSplit strips split rules", () => {
    const rules: SelectionRule[] = [
      { type: "gate" },
      { type: "split", train: 0.7, eval: 0.2, holdout: 0.1 },
    ];
    expect(rulesWithoutSplit(rules).length).toBe(1);
    expect(rulesWithoutSplit(rules)[0].type).toBe("gate");
  });
});
