import { describe, expect, it, vi } from "vitest";

import {
  executeTuiActivityCommandPlan,
  planTuiActivityCommand,
  resolveTuiActivityCommand,
} from "../src/tui/activity-command.js";

describe("TUI activity command resolver", () => {
  const current = {
    filter: "runtime",
    verbosity: "verbose",
  } as const;

  it("treats empty args and status as read-only status requests", () => {
    expect(resolveTuiActivityCommand("", current)).toEqual({
      kind: "status",
      settings: current,
    });
    expect(resolveTuiActivityCommand("status", current)).toEqual({
      kind: "status",
      settings: current,
    });
  });

  it("resolves reset without mixing it with update arguments", () => {
    expect(resolveTuiActivityCommand("reset", current)).toEqual({
      kind: "reset",
    });
    expect(resolveTuiActivityCommand("reset quiet", current)).toEqual({
      kind: "invalid",
    });
  });

  it("resolves activity setting updates from filter and verbosity tokens", () => {
    expect(resolveTuiActivityCommand("commands quiet", current)).toEqual({
      kind: "update",
      settings: {
        filter: "commands",
        verbosity: "quiet",
      },
    });
    expect(resolveTuiActivityCommand("normal", current)).toEqual({
      kind: "update",
      settings: {
        filter: "runtime",
        verbosity: "normal",
      },
    });
  });

  it("rejects unknown or over-specified arguments", () => {
    expect(resolveTuiActivityCommand("chatter", current)).toEqual({
      kind: "invalid",
    });
    expect(resolveTuiActivityCommand("runtime quiet verbose", current)).toEqual({
      kind: "invalid",
    });
  });
});

describe("TUI activity command executor", () => {
  const current = {
    filter: "runtime",
    verbosity: "verbose",
  } as const;

  it("renders read-only and usage plans without touching persistence", () => {
    const effects = {
      reset: vi.fn(),
      save: vi.fn(),
    };

    expect(executeTuiActivityCommandPlan({
      kind: "read",
      settings: current,
    }, effects)).toEqual({
      logLines: ["activity filter=runtime verbosity=verbose"],
    });
    expect(executeTuiActivityCommandPlan({
      kind: "usage",
      usageLine: "usage: /activity ...",
    }, effects)).toEqual({
      logLines: ["usage: /activity ..."],
    });
    expect(effects.reset).not.toHaveBeenCalled();
    expect(effects.save).not.toHaveBeenCalled();
  });

  it("resets persisted settings and returns the next activity state", () => {
    const nextSettings = {
      filter: "all",
      verbosity: "normal",
    } as const;
    const effects = {
      reset: vi.fn(() => nextSettings),
      save: vi.fn(),
    };

    expect(executeTuiActivityCommandPlan({ kind: "reset" }, effects)).toEqual({
      logLines: ["activity filter=all verbosity=normal"],
      activitySettings: nextSettings,
    });
    expect(effects.reset).toHaveBeenCalledOnce();
    expect(effects.save).not.toHaveBeenCalled();
  });

  it("saves planned settings and returns the next activity state", () => {
    const nextSettings = {
      filter: "commands",
      verbosity: "quiet",
    } as const;
    const effects = {
      reset: vi.fn(),
      save: vi.fn(),
    };

    expect(executeTuiActivityCommandPlan({
      kind: "save",
      settings: nextSettings,
    }, effects)).toEqual({
      logLines: ["activity filter=commands verbosity=quiet"],
      activitySettings: nextSettings,
    });
    expect(effects.save).toHaveBeenCalledWith(nextSettings);
    expect(effects.reset).not.toHaveBeenCalled();
  });

  it("ignores unhandled plans without touching persistence", () => {
    const effects = {
      reset: vi.fn(),
      save: vi.fn(),
    };

    expect(executeTuiActivityCommandPlan({ kind: "unhandled" }, effects)).toBeNull();
    expect(effects.reset).not.toHaveBeenCalled();
    expect(effects.save).not.toHaveBeenCalled();
  });
});

describe("TUI activity command planner", () => {
  const current = {
    filter: "runtime",
    verbosity: "verbose",
  } as const;

  it("plans read-only status commands from exact /activity commands", () => {
    expect(planTuiActivityCommand("/activity", current)).toEqual({
      kind: "read",
      settings: current,
    });
    expect(planTuiActivityCommand("  /activity status  ", current)).toEqual({
      kind: "read",
      settings: current,
    });
  });

  it("plans reset and save effects separately from persistence", () => {
    expect(planTuiActivityCommand("/activity reset", current)).toEqual({
      kind: "reset",
    });
    expect(planTuiActivityCommand("/activity commands quiet", current)).toEqual({
      kind: "save",
      settings: {
        filter: "commands",
        verbosity: "quiet",
      },
    });
  });

  it("returns usage plans for invalid activity arguments", () => {
    expect(planTuiActivityCommand("/activity chatter", current)).toEqual({
      kind: "usage",
      usageLine: "usage: /activity [status|reset|<all|runtime|prompts|commands|children|errors> [quiet|normal|verbose]]",
    });
  });

  it("leaves similarly prefixed commands unhandled", () => {
    expect(planTuiActivityCommand("/activityx", current)).toEqual({
      kind: "unhandled",
    });
    expect(planTuiActivityCommand("/activitystatus", current)).toEqual({
      kind: "unhandled",
    });
  });
});
