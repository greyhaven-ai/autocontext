import { describe, expect, it, vi } from "vitest";

import {
  executeSolveCommandWorkflow,
  planSolveCommand,
  renderSolveCommandSummary,
} from "../src/cli/solve-command-workflow.js";

describe("solve command workflow", () => {
  it("plans required description, generations, timeout, and JSON output", () => {
    const parsePositiveInteger = vi.fn((raw: string | undefined) => Number(raw));

    expect(
      planSolveCommand(
        {
          description: "  investigate checkout failures  ",
          gens: "3",
          timeout: "12",
          json: true,
        },
        parsePositiveInteger,
      ),
    ).toEqual({
      description: "investigate checkout failures",
      generations: 3,
      timeoutMs: 12_000,
      json: true,
    });
    expect(parsePositiveInteger).toHaveBeenCalledWith("3", "--gens");
    expect(parsePositiveInteger).toHaveBeenCalledWith("12", "--timeout");
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
        json: true,
      },
      sleep: vi.fn(async () => undefined),
      pollIntervalMs: 1,
    });

    expect(submit).toHaveBeenCalledWith("grid ctf", 1);
    expect(getStatus).toHaveBeenCalledTimes(2);
    expect(summary).toEqual({
      jobId: "solve-123",
      status: "completed",
      description: "grid ctf",
      scenarioName: "grid_ctf",
      family: "game",
      generations: 1,
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
      progress: 1,
      result: { scenario_name: "grid_ctf" },
    };

    expect(JSON.parse(renderSolveCommandSummary(summary, true))).toEqual(summary);
    expect(renderSolveCommandSummary(summary, false)).toContain("Solve completed");
  });
});
