import { describe, expect, it, vi } from "vitest";

import {
  executeSolveCommandWorkflow,
  planSolveCommand,
  renderSolveCommandSummary,
  writeSolveOutputFile,
} from "../src/cli/solve-command-workflow.js";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

describe("solve command workflow", () => {
  it("plans required description, generations, timeout, and JSON output", () => {
    const parsePositiveInteger = vi.fn((raw: string | undefined) => Number(raw));

    expect(
      planSolveCommand(
        {
          description: "  investigate checkout failures  ",
          gens: "3",
          timeout: "12",
          "generation-time-budget": "4",
          family: "investigation",
          output: "solve-result.json",
          json: true,
        },
        parsePositiveInteger,
      ),
    ).toEqual({
      description: "investigate checkout failures",
      generations: 3,
      timeoutMs: 12_000,
      generationTimeBudgetSeconds: 4,
      familyOverride: "investigation",
      outputPath: "solve-result.json",
      json: true,
    });
    expect(parsePositiveInteger).toHaveBeenCalledWith("3", "--gens");
    expect(parsePositiveInteger).toHaveBeenCalledWith("12", "--timeout");
  });

  it("accepts a plain-language positional description", () => {
    expect(
      planSolveCommand(
        {
          positionals: ["build an orbital transfer optimizer"],
          gens: "2",
        },
        (raw: string | undefined) => Number(raw),
      ),
    ).toMatchObject({
      description: "build an orbital transfer optimizer",
      generations: 2,
    });
  });

  it("accepts iterations as a plain-language alias for generations", () => {
    const parsePositiveInteger = vi.fn((raw: string | undefined) => Number(raw));

    expect(
      planSolveCommand(
        {
          positionals: ["build an orbital transfer optimizer"],
          iterations: "4",
        },
        parsePositiveInteger,
      ),
    ).toMatchObject({
      generations: 4,
    });
    expect(parsePositiveInteger).toHaveBeenCalledWith("4", "--iterations");
  });

  it("prefers precise gens over iterations when both are present", () => {
    expect(
      planSolveCommand(
        {
          description: "explicit task",
          gens: "3",
          iterations: "4",
        },
        (raw: string | undefined) => Number(raw),
      ).generations,
    ).toBe(3);
  });

  it("prefers explicit descriptions over positional shorthand", () => {
    expect(
      planSolveCommand(
        {
          description: "  explicit task  ",
          positionals: ["positional task"],
        },
        () => 1,
      ).description,
    ).toBe("explicit task");
  });

  it("rejects missing descriptions", () => {
    expect(() =>
      planSolveCommand({}, () => 1),
    ).toThrow("--description is required");
  });

  it("submits a solve job and waits for completion", async () => {
    const submit = vi.fn(() => "solve-123");
    const getStatus = vi
      .fn()
      .mockReturnValueOnce({ jobId: "solve-123", status: "running", progress: 0 })
      .mockReturnValueOnce({
        jobId: "solve-123",
        status: "completed",
        description: "grid ctf",
        scenarioName: "grid_ctf",
        family: "game",
        generations: 1,
        progress: 1,
      });
    const getResult = vi.fn(() => ({ scenario_name: "grid_ctf" }));

    const summary = await executeSolveCommandWorkflow({
      manager: { submit, getStatus, getResult },
      plan: {
        description: "grid ctf",
        generations: 1,
        timeoutMs: 1000,
        generationTimeBudgetSeconds: 7,
        familyOverride: "game",
        outputPath: "result.json",
        json: true,
      },
      sleep: vi.fn(async () => undefined),
      pollIntervalMs: 1,
    });

    expect(submit).toHaveBeenCalledWith("grid ctf", 1, {
      familyOverride: "game",
      generationTimeBudgetSeconds: 7,
    });
    expect(getStatus).toHaveBeenCalledTimes(2);
    expect(summary).toEqual({
      jobId: "solve-123",
      status: "completed",
      description: "grid ctf",
      scenarioName: "grid_ctf",
      family: "game",
      generations: 1,
      generationTimeBudgetSeconds: 7,
      outputPath: "result.json",
      llmClassifierFallbackUsed: false,
      progress: 1,
      result: { scenario_name: "grid_ctf" },
    });
  });

  it("renders structured JSON or concise text", () => {
    const summary = {
      jobId: "solve-123",
      status: "completed",
      description: "grid ctf",
      scenarioName: "grid_ctf",
      family: "game",
      generations: 1,
      generationTimeBudgetSeconds: null,
      outputPath: null,
      llmClassifierFallbackUsed: false,
      progress: 1,
      result: { scenario_name: "grid_ctf" },
    };

    expect(JSON.parse(renderSolveCommandSummary(summary, true))).toEqual(summary);
    expect(renderSolveCommandSummary(summary, false)).toContain("Solve completed");
  });

  it("writes solved package output JSON files", () => {
    const dir = mkdtempSync(join(tmpdir(), "ac-solve-output-"));
    try {
      const outputPath = join(dir, "package.json");
      writeSolveOutputFile({ scenario_name: "grid_ctf" }, outputPath);
      expect(existsSync(outputPath)).toBe(true);
      expect(JSON.parse(readFileSync(outputPath, "utf-8"))).toEqual({
        scenario_name: "grid_ctf",
      });
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
