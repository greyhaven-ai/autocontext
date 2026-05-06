import { describe, expect, it, vi } from "vitest";

import {
  executeTuiSolveCommandPlan,
  planTuiSolveCommand,
} from "../src/tui/solve-command.js";

describe("TUI solve command planner", () => {
  it("plans quoted and unquoted plain-language descriptions", () => {
    expect(planTuiSolveCommand('/solve "build an orbital transfer optimizer"')).toEqual({
      kind: "solve",
      description: "build an orbital transfer optimizer",
      iterations: 5,
    });
    expect(planTuiSolveCommand("/solve improve billing replies")).toEqual({
      kind: "solve",
      description: "improve billing replies",
      iterations: 5,
    });
  });

  it("trims quoted description content", () => {
    expect(planTuiSolveCommand('/solve "  improve billing replies  "')).toEqual({
      kind: "solve",
      description: "improve billing replies",
      iterations: 5,
    });
  });

  it("returns usage for whitespace-only descriptions", () => {
    expect(planTuiSolveCommand('/solve "   "')).toEqual({
      kind: "usage",
      usageLine: 'usage: /solve "plain-language goal"',
    });
  });

  it("leaves bare or similarly prefixed commands unhandled", () => {
    expect(planTuiSolveCommand("/solve")).toEqual({ kind: "unhandled" });
    expect(planTuiSolveCommand("/solver help")).toEqual({ kind: "unhandled" });
  });
});

describe("TUI solve command executor", () => {
  it("creates, confirms, and starts a generated scenario through a narrow command port", async () => {
    const effects = {
      createScenario: vi.fn(async () => ({ name: "draft_orbital_transfer" })),
      confirmScenario: vi.fn(async () => ({ name: "orbital_transfer" })),
      startRun: vi.fn(async () => "run-123"),
    };

    await expect(executeTuiSolveCommandPlan({
      kind: "solve",
      description: "build an orbital transfer optimizer",
      iterations: 5,
    }, effects)).resolves.toEqual({
      logLines: [
        "created scenario draft_orbital_transfer",
        "accepted run run-123",
      ],
    });
    expect(effects.createScenario).toHaveBeenCalledWith("build an orbital transfer optimizer");
    expect(effects.confirmScenario).toHaveBeenCalledOnce();
    expect(effects.startRun).toHaveBeenCalledWith("orbital_transfer", 5);
  });

  it("reports usage and ignores unhandled plans without mutating the run manager", async () => {
    const effects = {
      createScenario: vi.fn(),
      confirmScenario: vi.fn(),
      startRun: vi.fn(),
    };

    await expect(executeTuiSolveCommandPlan({
      kind: "usage",
      usageLine: 'usage: /solve "plain-language goal"',
    }, effects)).resolves.toEqual({
      logLines: ['usage: /solve "plain-language goal"'],
    });
    await expect(executeTuiSolveCommandPlan({ kind: "unhandled" }, effects)).resolves.toBeNull();
    expect(effects.createScenario).not.toHaveBeenCalled();
    expect(effects.confirmScenario).not.toHaveBeenCalled();
    expect(effects.startRun).not.toHaveBeenCalled();
  });

  it("maps scenario creation failures to log lines", async () => {
    await expect(executeTuiSolveCommandPlan({
      kind: "solve",
      description: "build a scenario",
      iterations: 5,
    }, {
      createScenario: vi.fn(async () => {
        throw new Error("designer unavailable");
      }),
      confirmScenario: vi.fn(),
      startRun: vi.fn(),
    })).resolves.toEqual({
      logLines: ["designer unavailable"],
    });
  });
});
