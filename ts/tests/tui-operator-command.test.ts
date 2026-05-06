import { describe, expect, it, vi } from "vitest";

import { handleInteractiveTuiCommand } from "../src/tui/commands.js";
import {
  executeTuiOperatorCommandPlan,
  formatTuiScenarioList,
  planTuiOperatorCommand,
} from "../src/tui/operator-command.js";

describe("TUI operator command planner", () => {
  it("plans exact pause and resume commands", () => {
    expect(planTuiOperatorCommand("/pause")).toEqual({ kind: "pause" });
    expect(planTuiOperatorCommand("  /resume  ")).toEqual({ kind: "resume" });
  });

  it("plans exact scenario listing commands", () => {
    expect(planTuiOperatorCommand("/scenarios")).toEqual({ kind: "listScenarios" });
  });

  it("plans operator hints with trimmed text", () => {
    expect(planTuiOperatorCommand("/hint Focus on rollback safety")).toEqual({
      kind: "injectHint",
      text: "Focus on rollback safety",
    });
    expect(planTuiOperatorCommand("  /hint   Try a smaller patch  ")).toEqual({
      kind: "injectHint",
      text: "Try a smaller patch",
    });
  });

  it("plans valid gate overrides and rejects invalid decisions", () => {
    expect(planTuiOperatorCommand("/gate advance")).toEqual({
      kind: "overrideGate",
      decision: "advance",
    });
    expect(planTuiOperatorCommand("/gate retry")).toEqual({
      kind: "overrideGate",
      decision: "retry",
    });
    expect(planTuiOperatorCommand("/gate rollback")).toEqual({
      kind: "overrideGate",
      decision: "rollback",
    });
    expect(planTuiOperatorCommand("/gate hold")).toEqual({ kind: "invalidGate" });
    expect(planTuiOperatorCommand("/gate retry now")).toEqual({ kind: "invalidGate" });
  });

  it("leaves similarly prefixed or argument-bearing commands unhandled", () => {
    expect(planTuiOperatorCommand("/pause now")).toEqual({ kind: "unhandled" });
    expect(planTuiOperatorCommand("/resumed")).toEqual({ kind: "unhandled" });
    expect(planTuiOperatorCommand("/scenarios grid")).toEqual({ kind: "unhandled" });
    expect(planTuiOperatorCommand("/hint")).toEqual({ kind: "unhandled" });
    expect(planTuiOperatorCommand("/gate")).toEqual({ kind: "unhandled" });
  });

  it("formats scenario list output consistently", () => {
    expect(formatTuiScenarioList(["grid_ctf", "othello"])).toBe("scenarios: grid_ctf, othello");
  });
});

describe("TUI operator command executor", () => {
  it("applies pause and resume through a narrow command port", () => {
    const effects = {
      pause: vi.fn(),
      resume: vi.fn(),
      listScenarios: vi.fn(),
      injectHint: vi.fn(),
      overrideGate: vi.fn(),
    };

    expect(executeTuiOperatorCommandPlan({ kind: "pause" }, effects)).toEqual({
      logLines: ["paused active loop"],
    });
    expect(effects.pause).toHaveBeenCalledOnce();

    expect(executeTuiOperatorCommandPlan({ kind: "resume" }, effects)).toEqual({
      logLines: ["resumed active loop"],
    });
    expect(effects.resume).toHaveBeenCalledOnce();
  });

  it("renders scenario lists without exposing the full run manager", () => {
    const effects = {
      pause: vi.fn(),
      resume: vi.fn(),
      listScenarios: vi.fn(() => ["grid_ctf", "othello"]),
      injectHint: vi.fn(),
      overrideGate: vi.fn(),
    };

    expect(executeTuiOperatorCommandPlan({ kind: "listScenarios" }, effects)).toEqual({
      logLines: ["scenarios: grid_ctf, othello"],
    });
    expect(effects.listScenarios).toHaveBeenCalledOnce();
  });

  it("applies hint and gate overrides through the command port", () => {
    const effects = {
      pause: vi.fn(),
      resume: vi.fn(),
      listScenarios: vi.fn(),
      injectHint: vi.fn(),
      overrideGate: vi.fn(),
    };

    expect(executeTuiOperatorCommandPlan({
      kind: "injectHint",
      text: "Focus on rollback safety",
    }, effects)).toEqual({
      logLines: ["operator hint queued"],
    });
    expect(effects.injectHint).toHaveBeenCalledWith("Focus on rollback safety");

    expect(executeTuiOperatorCommandPlan({
      kind: "overrideGate",
      decision: "retry",
    }, effects)).toEqual({
      logLines: ["gate override queued: retry"],
    });
    expect(effects.overrideGate).toHaveBeenCalledWith("retry");
  });

  it("reports invalid gates and ignores unhandled plans without mutating effects", () => {
    const effects = {
      pause: vi.fn(),
      resume: vi.fn(),
      listScenarios: vi.fn(),
      injectHint: vi.fn(),
      overrideGate: vi.fn(),
    };

    expect(executeTuiOperatorCommandPlan({ kind: "invalidGate" }, effects)).toEqual({
      logLines: ["gate override must be advance|retry|rollback"],
    });
    expect(effects.overrideGate).not.toHaveBeenCalled();

    expect(executeTuiOperatorCommandPlan({ kind: "unhandled" }, effects)).toBeNull();
    expect(effects.pause).not.toHaveBeenCalled();
    expect(effects.resume).not.toHaveBeenCalled();
    expect(effects.listScenarios).not.toHaveBeenCalled();
    expect(effects.injectHint).not.toHaveBeenCalled();
  });
});

describe("TUI operator command handler", () => {
  it("applies pause and resume plans through the run manager", async () => {
    const manager = {
      pause: vi.fn(),
      resume: vi.fn(),
    };

    await expect(handleInteractiveTuiCommand({
      manager: manager as never,
      configDir: ".",
      raw: "/pause",
      pendingLogin: null,
    })).resolves.toMatchObject({ logLines: ["paused active loop"] });
    expect(manager.pause).toHaveBeenCalledOnce();

    await expect(handleInteractiveTuiCommand({
      manager: manager as never,
      configDir: ".",
      raw: "/resume",
      pendingLogin: null,
    })).resolves.toMatchObject({ logLines: ["resumed active loop"] });
    expect(manager.resume).toHaveBeenCalledOnce();
  });

  it("renders scenario lists through the run manager", async () => {
    const manager = {
      listScenarios: vi.fn(() => ["grid_ctf", "othello"]),
    };

    await expect(handleInteractiveTuiCommand({
      manager: manager as never,
      configDir: ".",
      raw: "/scenarios",
      pendingLogin: null,
    })).resolves.toMatchObject({ logLines: ["scenarios: grid_ctf, othello"] });
  });

  it("applies hint and gate plans through the run manager", async () => {
    const manager = {
      injectHint: vi.fn(),
      overrideGate: vi.fn(),
    };

    await expect(handleInteractiveTuiCommand({
      manager: manager as never,
      configDir: ".",
      raw: "/hint Focus on rollback safety",
      pendingLogin: null,
    })).resolves.toMatchObject({ logLines: ["operator hint queued"] });
    expect(manager.injectHint).toHaveBeenCalledWith("Focus on rollback safety");

    await expect(handleInteractiveTuiCommand({
      manager: manager as never,
      configDir: ".",
      raw: "/gate retry",
      pendingLogin: null,
    })).resolves.toMatchObject({ logLines: ["gate override queued: retry"] });
    expect(manager.overrideGate).toHaveBeenCalledWith("retry");
  });

  it("reports invalid gate decisions without applying overrides", async () => {
    const manager = {
      overrideGate: vi.fn(),
    };

    await expect(handleInteractiveTuiCommand({
      manager: manager as never,
      configDir: ".",
      raw: "/gate hold",
      pendingLogin: null,
    })).resolves.toMatchObject({ logLines: ["gate override must be advance|retry|rollback"] });
    expect(manager.overrideGate).not.toHaveBeenCalled();
  });
});
