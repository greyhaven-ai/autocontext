import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import { loadRunMessagesFromArtifacts } from "../src/traces/export-run-artifact-workflow.js";

describe("export run artifact workflow", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = mkdtempSync(join(tmpdir(), "ac-export-run-artifacts-"));
  });

  afterEach(() => {
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it("loads run metadata and generation artifact messages with expected roles", () => {
    const runDir = join(tmpDir, "run_1");
    const genDir = join(runDir, "generations", "gen_1");
    mkdirSync(genDir, { recursive: true });
    writeFileSync(join(runDir, "run_meta.json"), JSON.stringify({
      run_id: "run_1",
      scenario: "grid_ctf",
      created_at: "2026-03-27T10:00:00Z",
    }), "utf-8");
    writeFileSync(join(genDir, "competitor_prompt.md"), "Solve the problem", "utf-8");
    writeFileSync(join(genDir, "competitor_output.md"), "function solve() { return 42; }", "utf-8");
    writeFileSync(join(genDir, "trajectory.md"), "Score: 0.85", "utf-8");

    const result = loadRunMessagesFromArtifacts(runDir);
    expect(result.warnings).toEqual([]);
    expect(result.messages.map((message) => message.role)).toEqual([
      "system",
      "user",
      "assistant",
      "system",
    ]);
    expect(result.messages[0]?.content).toContain("Run run_1 for scenario grid_ctf");
  });

  it("surfaces unreadable or malformed artifacts as warnings instead of crashing", () => {
    const runDir = join(tmpDir, "run_warn");
    const genDir = join(runDir, "generations", "gen_1");
    mkdirSync(genDir, { recursive: true });
    writeFileSync(join(runDir, "run_meta.json"), "{not valid json", "utf-8");
    mkdirSync(join(genDir, "competitor_output.md"));
    writeFileSync(join(genDir, "analyst.md"), "usable analysis", "utf-8");

    const result = loadRunMessagesFromArtifacts(runDir);
    expect(result.messages).toHaveLength(1);
    expect(result.messages[0]?.content).toBe("usable analysis");
    expect(result.warnings).toHaveLength(2);
    expect(result.warnings.some((warning) => warning.includes("run_meta.json"))).toBe(true);
    expect(result.warnings.some((warning) => warning.includes("competitor_output.md"))).toBe(true);
  });
});
