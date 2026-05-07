import { describe, expect, test } from "vitest";
import { probeDirectoryContract } from "../../../src/control-plane/contract-probes/index.js";

describe("probeDirectoryContract", () => {
  test("reports unexpected and missing verifier-facing files", () => {
    const result = probeDirectoryContract({
      presentFiles: ["solution.txt", "main", "trace.log"],
      requiredFiles: ["solution.txt", "manifest.json"],
      allowedFiles: ["solution.txt", "manifest.json"],
      ignoredPatterns: [/^trace\./],
    });

    expect(result.passed).toBe(false);
    expect(result.failures).toEqual([
      {
        kind: "unexpected-file",
        path: "main",
        message: "unexpected file main",
      },
      {
        kind: "missing-file",
        path: "manifest.json",
        message: "required file manifest.json is missing",
      },
    ]);
  });
});
