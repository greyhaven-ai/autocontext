import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { JudgeServingSpecSchema, canonicalJudgeSpec, judgeSpecEpoch } from "../src/judge/judge-spec.js";

describe("judge serving specification wire contract", () => {
  const cases = JSON.parse(readFileSync(join(import.meta.dirname,
    "../../autocontext/tests/fixtures/judge-serving-spec-cases.json"), "utf8")).cases;

  it("matches Python canonical bytes and identities, including Unicode and ordered examples", () => {
    for (const fixture of cases) {
      const spec = JudgeServingSpecSchema.parse(fixture.spec);
      expect(canonicalJudgeSpec(spec)).toBe(fixture.canonical_json);
      expect(judgeSpecEpoch(spec)).toBe(fixture.epoch_id);
      expect(Object.isFrozen(spec)).toBe(true);
      expect(Object.isFrozen(spec.serving_examples)).toBe(true);
    }
    expect(new Set(cases.map((c: { epoch_id: string }) => c.epoch_id)).size).toBe(cases.length);
  });

  it("rejects unknown fields and incompatible versions", () => {
    expect(() => JudgeServingSpecSchema.parse({ ...cases[0].spec, api_key: "secret" })).toThrow();
    expect(() => JudgeServingSpecSchema.parse({ ...cases[0].spec, schema_version: "other" })).toThrow();
  });
});
