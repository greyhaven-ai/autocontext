import { describe, expect, it, vi } from "vitest";

import {
  executeTuiRunInspectionCommandPlan,
  executeTuiStartRunCommandPlan,
  planTuiRunInspectionCommand,
  planTuiStartRunCommand,
  resolveTuiRunCommandTarget,
} from "../src/tui/run-command.js";

describe("TUI run command target resolver", () => {
  it("uses the explicit run id before the active run id", () => {
    expect(resolveTuiRunCommandTarget("/timeline run-123", "run-active")).toEqual({
      kind: "target",
      runId: "run-123",
    });
  });

  it("falls back to the active run id when no explicit run id is present", () => {
    expect(resolveTuiRunCommandTarget("/timeline", "run-active")).toEqual({
      kind: "target",
      runId: "run-active",
    });
  });

  it("reports a missing target when neither command nor state provides a run id", () => {
    expect(resolveTuiRunCommandTarget("/timeline", null)).toEqual({
      kind: "missing",
    });
    expect(resolveTuiRunCommandTarget("/timeline", "")).toEqual({
      kind: "missing",
    });
  });

  it("keeps the first argument as the target for commands with trailing options", () => {
    expect(resolveTuiRunCommandTarget("/show run-123 --best", "run-active")).toEqual({
      kind: "target",
      runId: "run-123",
    });
  });
});

describe("TUI run inspection command planner", () => {
  it("plans status and watch commands with shared active-run fallback", () => {
    expect(planTuiRunInspectionCommand("/status run-123", "run-active")).toEqual({
      kind: "status",
      runId: "run-123",
    });
    expect(planTuiRunInspectionCommand("/watch", "run-active")).toEqual({
      kind: "watch",
      runId: "run-active",
    });
  });

  it("plans show commands with the best flag isolated from command effects", () => {
    expect(planTuiRunInspectionCommand("/show run-123 --best", "run-active")).toEqual({
      kind: "show",
      runId: "run-123",
      best: true,
    });
    expect(planTuiRunInspectionCommand("/show run-123", "run-active")).toEqual({
      kind: "show",
      runId: "run-123",
      best: false,
    });
  });

  it("plans runtime timeline commands with the same target rules", () => {
    expect(planTuiRunInspectionCommand("/timeline", "run-active")).toEqual({
      kind: "timeline",
      runId: "run-active",
    });
  });

  it("returns command-specific usage when a run target is missing", () => {
    expect(planTuiRunInspectionCommand("/timeline", null)).toEqual({
      kind: "usage",
      usageLine: "usage: /timeline <run-id>",
    });
    expect(planTuiRunInspectionCommand("/show", "")).toEqual({
      kind: "usage",
      usageLine: "usage: /show <run-id> [--best]",
    });
  });

  it("leaves non-run-inspection commands unhandled", () => {
    expect(planTuiRunInspectionCommand("/hint inspect this", "run-active")).toEqual({
      kind: "unhandled",
    });
    expect(planTuiRunInspectionCommand("/statusish run-123", "run-active")).toEqual({
      kind: "unhandled",
    });
  });

  it("does not read active run state for non-run-inspection commands", () => {
    let readCount = 0;

    expect(planTuiRunInspectionCommand("/login anthropic", () => {
      readCount += 1;
      return "run-active";
    })).toEqual({
      kind: "unhandled",
    });
    expect(readCount).toBe(0);
  });
});

describe("TUI run inspection command executor", () => {
  it("routes status and show plans through narrow render ports", async () => {
    const effects = {
      renderStatus: vi.fn(async () => ["status line"]),
      renderShow: vi.fn(async () => ["show line"]),
      renderTimeline: vi.fn(),
    };

    await expect(executeTuiRunInspectionCommandPlan({
      kind: "status",
      runId: "run-123",
    }, effects)).resolves.toEqual({
      logLines: ["status line"],
    });
    await expect(executeTuiRunInspectionCommandPlan({
      kind: "show",
      runId: "run-123",
      best: true,
    }, effects)).resolves.toEqual({
      logLines: ["show line"],
    });

    expect(effects.renderStatus).toHaveBeenCalledWith("run-123");
    expect(effects.renderShow).toHaveBeenCalledWith("run-123", true);
    expect(effects.renderTimeline).not.toHaveBeenCalled();
  });

  it("prepends the watching line while reusing status rendering", async () => {
    const effects = {
      renderStatus: vi.fn(async () => ["status line"]),
      renderShow: vi.fn(),
      renderTimeline: vi.fn(),
    };

    await expect(executeTuiRunInspectionCommandPlan({
      kind: "watch",
      runId: "run-123",
    }, effects)).resolves.toEqual({
      logLines: ["watching run-123", "status line"],
    });

    expect(effects.renderStatus).toHaveBeenCalledWith("run-123");
    expect(effects.renderShow).not.toHaveBeenCalled();
    expect(effects.renderTimeline).not.toHaveBeenCalled();
  });

  it("routes timeline plans through the timeline renderer", async () => {
    const effects = {
      renderStatus: vi.fn(),
      renderShow: vi.fn(),
      renderTimeline: vi.fn(async () => ["timeline line"]),
    };

    await expect(executeTuiRunInspectionCommandPlan({
      kind: "timeline",
      runId: "run-123",
    }, effects)).resolves.toEqual({
      logLines: ["timeline line"],
    });

    expect(effects.renderTimeline).toHaveBeenCalledWith("run-123");
    expect(effects.renderStatus).not.toHaveBeenCalled();
    expect(effects.renderShow).not.toHaveBeenCalled();
  });

  it("reports usage and ignores unhandled plans without touching render ports", async () => {
    const effects = {
      renderStatus: vi.fn(),
      renderShow: vi.fn(),
      renderTimeline: vi.fn(),
    };

    await expect(executeTuiRunInspectionCommandPlan({
      kind: "usage",
      usageLine: "usage: /status <run-id>",
    }, effects)).resolves.toEqual({
      logLines: ["usage: /status <run-id>"],
    });
    await expect(executeTuiRunInspectionCommandPlan({ kind: "unhandled" }, effects)).resolves.toBeNull();

    expect(effects.renderStatus).not.toHaveBeenCalled();
    expect(effects.renderShow).not.toHaveBeenCalled();
    expect(effects.renderTimeline).not.toHaveBeenCalled();
  });

  it("maps render failures to log lines", async () => {
    const effects = {
      renderStatus: vi.fn(async () => {
        throw new Error("run 'missing' not found");
      }),
      renderShow: vi.fn(),
      renderTimeline: vi.fn(),
    };

    await expect(executeTuiRunInspectionCommandPlan({
      kind: "status",
      runId: "missing",
    }, effects)).resolves.toEqual({
      logLines: ["run 'missing' not found"],
    });
  });
});

describe("TUI start run command planner", () => {
  it("plans scenario runs with an explicit iteration count", () => {
    expect(planTuiStartRunCommand("/run support_triage 3")).toEqual({
      kind: "start",
      scenario: "support_triage",
      iterations: 3,
    });
  });

  it("defaults missing or invalid iteration text to five", () => {
    expect(planTuiStartRunCommand("/run support_triage")).toEqual({
      kind: "start",
      scenario: "support_triage",
      iterations: 5,
    });
    expect(planTuiStartRunCommand("/run support_triage many")).toEqual({
      kind: "start",
      scenario: "support_triage",
      iterations: 5,
    });
  });

  it("keeps current token parsing behavior for numeric prefixes and trailing tokens", () => {
    expect(planTuiStartRunCommand("/run support_triage 7extra ignored")).toEqual({
      kind: "start",
      scenario: "support_triage",
      iterations: 7,
    });
  });

  it("leaves bare or similarly-prefixed commands unhandled", () => {
    expect(planTuiStartRunCommand("/run")).toEqual({
      kind: "unhandled",
    });
    expect(planTuiStartRunCommand("/runner support_triage")).toEqual({
      kind: "unhandled",
    });
  });
});

describe("TUI start run command executor", () => {
  it("routes start plans through a narrow command port and formats accepted runs", async () => {
    const effects = {
      startRun: vi.fn(async () => "run-123"),
    };

    await expect(executeTuiStartRunCommandPlan({
      kind: "start",
      scenario: "support_triage",
      iterations: 3,
    }, effects)).resolves.toEqual({
      logLines: ["accepted run run-123"],
    });
    expect(effects.startRun).toHaveBeenCalledWith("support_triage", 3);
  });

  it("ignores unhandled plans without calling the run manager", async () => {
    const effects = {
      startRun: vi.fn(),
    };

    await expect(executeTuiStartRunCommandPlan({ kind: "unhandled" }, effects)).resolves.toBeNull();
    expect(effects.startRun).not.toHaveBeenCalled();
  });

  it("maps start failures to log lines", async () => {
    const effects = {
      startRun: vi.fn(async () => {
        throw new Error("scenario not found");
      }),
    };

    await expect(executeTuiStartRunCommandPlan({
      kind: "start",
      scenario: "missing",
      iterations: 5,
    }, effects)).resolves.toEqual({
      logLines: ["scenario not found"],
    });
  });
});
