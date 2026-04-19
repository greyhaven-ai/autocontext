import { describe, test, expect, vi } from "vitest";
import { resolveRubric } from "../../../../src/production-traces/dataset/rubric.js";
import { makeTrace } from "./_helpers/fixtures.js";
import type {
  Rubric,
  RubricConfig,
  RubricLookup,
} from "../../../../src/production-traces/dataset/types.js";
import { writeFileSync, mkdtempSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { parseScenario } from "../../../../src/production-traces/contract/branded-ids.js";

describe("resolveRubric precedence", () => {
  const goodRubric: Rubric = {
    rubricId: "explicit-rubric",
    dimensions: ["accuracy", "helpfulness"],
  };

  test("source 1 (explicit inline) wins over all others", async () => {
    const config: RubricConfig = {
      rubricsByCluster: {
        checkout: { source: "inline", rubric: goodRubric },
      },
    };
    const lookup: RubricLookup = vi.fn(async () => ({
      rubricId: "registry",
      dimensions: ["x"],
    }));
    const result = await resolveRubric(
      "checkout",
      [makeTrace({ scenarioId: "s1" })],
      config,
      lookup,
      { allowSynthetic: true },
    );
    expect(result.source).toBe("explicit");
    if (result.source === "explicit") {
      expect(result.rubric.rubricId).toBe("explicit-rubric");
    }
    expect(lookup).not.toHaveBeenCalled();
  });

  test("source 1 (explicit file) loads rubric from disk", async () => {
    const dir = mkdtempSync(join(tmpdir(), "rubric-"));
    const path = join(dir, "r.json");
    writeFileSync(path, JSON.stringify(goodRubric));
    const config: RubricConfig = {
      rubricsByCluster: {
        checkout: { source: "file", path },
      },
    };
    const result = await resolveRubric(
      "checkout",
      [],
      config,
      undefined,
      { allowSynthetic: false },
    );
    expect(result.source).toBe("explicit");
  });

  test("source 2 (registry) used when no explicit entry", async () => {
    const registryRubric: Rubric = { rubricId: "registry-rubric", dimensions: ["x"] };
    const lookup: RubricLookup = vi.fn(async () => registryRubric);
    const result = await resolveRubric(
      "any-cluster",
      [makeTrace({ scenarioId: "my-scenario" })],
      undefined,
      lookup,
      { allowSynthetic: false },
    );
    expect(result.source).toBe("registry");
    expect(lookup).toHaveBeenCalledWith(parseScenario("my-scenario"));
  });

  test("source 2 skipped if no trace has scenarioId", async () => {
    const lookup: RubricLookup = vi.fn(async () => ({ rubricId: "r", dimensions: ["x"] }));
    const result = await resolveRubric(
      "any-cluster",
      [makeTrace({})],
      undefined,
      lookup,
      { allowSynthetic: false },
    );
    expect(result.source).toBe("skip");
    expect(lookup).not.toHaveBeenCalled();
  });

  test("source 3 (synthetic) only when allowSynthetic=true and ≥50% labeled", async () => {
    const traces = [
      makeTrace({ traceId: "01K00000000000000000000001", outcome: { label: "success" } }),
      makeTrace({ traceId: "01K00000000000000000000002", outcome: { label: "failure" } }),
      makeTrace({ traceId: "01K00000000000000000000003" }),
    ];
    const result = await resolveRubric("x", traces, undefined, undefined, { allowSynthetic: true });
    expect(result.source).toBe("synthetic");
    if (result.source === "synthetic") {
      expect(result.rubric.rubricId).toBe("synthetic-x");
      expect(result.rubric.dimensions).toContain("label_match");
    }
  });

  test("synthetic refused when <50% labeled", async () => {
    const traces = [
      makeTrace({ traceId: "01K00000000000000000000001", outcome: { label: "success" } }),
      makeTrace({ traceId: "01K00000000000000000000002" }),
      makeTrace({ traceId: "01K00000000000000000000003" }),
    ];
    const result = await resolveRubric("x", traces, undefined, undefined, { allowSynthetic: true });
    expect(result.source).toBe("skip");
  });

  test("synthetic opt-in required: default is skip", async () => {
    const traces = [
      makeTrace({ traceId: "01K00000000000000000000001", outcome: { label: "success" } }),
    ];
    const result = await resolveRubric("x", traces, undefined, undefined, { allowSynthetic: false });
    expect(result.source).toBe("skip");
  });

  test("registry lookup called with correct scenarioId (first matching trace)", async () => {
    const lookup: RubricLookup = vi.fn(async () => null);
    const traces = [
      makeTrace({ traceId: "01K00000000000000000000001" }),
      makeTrace({ traceId: "01K00000000000000000000002", scenarioId: "scenario-a" }),
      makeTrace({ traceId: "01K00000000000000000000003", scenarioId: "scenario-b" }),
    ];
    await resolveRubric("x", traces, undefined, lookup, { allowSynthetic: false });
    // Lookup is called sequentially until a match returns non-null;
    // since the mock returns null twice, both scenarios should be tried.
    expect(lookup).toHaveBeenCalledWith(parseScenario("scenario-a"));
    expect(lookup).toHaveBeenCalledWith(parseScenario("scenario-b"));
  });

  test("malformed explicit file produces skip with reason", async () => {
    const dir = mkdtempSync(join(tmpdir(), "rubric-"));
    const path = join(dir, "bad.json");
    writeFileSync(path, "not a rubric");
    const config: RubricConfig = {
      rubricsByCluster: {
        x: { source: "file", path },
      },
    };
    const result = await resolveRubric("x", [], config, undefined, { allowSynthetic: false });
    expect(result.source).toBe("skip");
    if (result.source === "skip") {
      expect(result.skipReason).toMatch(/explicit rubric load failed/);
    }
  });
});
