import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

import { formatCommandHelp, handleInteractiveTuiCommand } from "../src/tui/commands.js";
import { DEFAULT_TUI_ACTIVITY_SETTINGS } from "../src/tui/activity-summary.js";
import {
  loadTuiActivitySettings,
  saveTuiActivitySettings,
  TUI_SETTINGS_FILE,
} from "../src/tui/activity-settings-store.js";
import {
  RuntimeSessionEventLog,
  RuntimeSessionEventStore,
  RuntimeSessionEventType,
} from "../src/session/runtime-events.js";
import { runtimeSessionIdForRun } from "../src/session/runtime-session-ids.js";

describe("TUI command help", () => {
  it("uses the same plain-language concepts as the CLI contract", () => {
    const help = formatCommandHelp().join("\n");

    expect(help).toContain('/solve "plain-language goal"');
    expect(help).toContain("/run <scenario> [iterations]");
    expect(help).toContain("/status <run-id>");
    expect(help).toContain("/show <run-id> --best");
    expect(help).toContain("/watch <run-id>");
    expect(help).toContain("/timeline <run-id>");
    expect(help).toContain("/activity [status|reset|<all|runtime|prompts|commands|children|errors> [quiet|normal|verbose]]");
  });

  it("turns /solve plain language into scenario creation and a run", async () => {
    const manager = {
      createScenario: vi.fn(async () => ({ name: "orbital_transfer" })),
      confirmScenario: vi.fn(async () => ({ name: "orbital_transfer", testScores: [] })),
      startRun: vi.fn(async () => "run-123"),
    };

    const result = await handleInteractiveTuiCommand({
      manager: manager as never,
      configDir: ".",
      raw: '/solve "build an orbital transfer optimizer"',
      pendingLogin: null,
    });

    expect(manager.createScenario).toHaveBeenCalledWith("build an orbital transfer optimizer");
    expect(manager.startRun).toHaveBeenCalledWith("orbital_transfer", 5);
    expect(result.logLines).toContain("accepted run run-123");
  });

  it("renders the active run runtime-session timeline", async () => {
    const dbPath = join(mkdtempSync(join(tmpdir(), "tui-runtime-session-")), "events.db");
    const store = new RuntimeSessionEventStore(dbPath);
    const log = RuntimeSessionEventLog.fromJSON({
      sessionId: runtimeSessionIdForRun("run-123"),
      parentSessionId: "",
      taskId: "",
      workerId: "",
      metadata: { goal: "autoctx run support_triage", runId: "run-123" },
      createdAt: "2026-04-10T00:00:00.000Z",
      updatedAt: "2026-04-10T00:00:02.000Z",
      events: [
        {
          eventId: "event-1",
          sessionId: runtimeSessionIdForRun("run-123"),
          sequence: 0,
          eventType: RuntimeSessionEventType.PROMPT_SUBMITTED,
          timestamp: "2026-04-10T00:00:01.000Z",
          payload: { role: "architect", prompt: "Improve the operator timeline" },
          parentSessionId: "",
          taskId: "",
          workerId: "",
        },
        {
          eventId: "event-2",
          sessionId: runtimeSessionIdForRun("run-123"),
          sequence: 1,
          eventType: RuntimeSessionEventType.ASSISTANT_MESSAGE,
          timestamp: "2026-04-10T00:00:02.000Z",
          payload: { role: "architect", text: "Group prompt and response turns." },
          parentSessionId: "",
          taskId: "",
          workerId: "",
        },
      ],
    });
    store.save(log);
    store.close();
    const manager = {
      getState: vi.fn(() => ({ runId: "run-123" })),
      getDbPath: vi.fn(() => dbPath),
    };

    const result = await handleInteractiveTuiCommand({
      manager: manager as never,
      configDir: ".",
      raw: "/timeline",
      pendingLogin: null,
    });

    expect(result.logLines).toEqual(
      expect.arrayContaining([
        "Runtime session timeline run:run-123:runtime",
        "0-1  prompt  completed  role=architect  prompt=Improve the operator timeline  response=Group prompt and response turns.",
      ]),
    );
  });

  it("updates live activity feed focus and verbosity", async () => {
    const manager = {};
    const configDir = mkdtempSync(join(tmpdir(), "tui-activity-command-"));

    const result = await handleInteractiveTuiCommand({
      manager: manager as never,
      configDir,
      raw: "/activity commands quiet",
      pendingLogin: null,
      activitySettings: { filter: "all", verbosity: "normal" },
    });

    expect(result.activitySettings).toEqual({
      filter: "commands",
      verbosity: "quiet",
    });
    expect(result.logLines).toEqual(["activity filter=commands verbosity=quiet"]);
    expect(loadTuiActivitySettings(configDir)).toEqual({
      filter: "commands",
      verbosity: "quiet",
    });
  });

  it("shows current live activity feed settings without creating persisted settings", async () => {
    const manager = {};
    const configDir = mkdtempSync(join(tmpdir(), "tui-activity-readonly-missing-"));

    const result = await handleInteractiveTuiCommand({
      manager: manager as never,
      configDir,
      raw: "/activity",
      pendingLogin: null,
      activitySettings: { filter: "runtime", verbosity: "verbose" },
    });

    expect(result.activitySettings).toBeUndefined();
    expect(result.logLines).toEqual(["activity filter=runtime verbosity=verbose"]);
    expect(existsSync(join(configDir, TUI_SETTINGS_FILE))).toBe(false);
  });

  it("shows current live activity feed settings without rewriting persisted settings", async () => {
    const manager = {};
    const configDir = mkdtempSync(join(tmpdir(), "tui-activity-readonly-existing-"));
    const settingsPath = join(configDir, TUI_SETTINGS_FILE);
    writeFileSync(
      settingsPath,
      `${JSON.stringify({
        activity: {
          filter: "commands",
          verbosity: "quiet",
        },
        updatedAt: "2026-04-10T00:00:00.000Z",
      }, null, 2)}\n`,
      "utf-8",
    );
    const before = readFileSync(settingsPath, "utf-8");

    const result = await handleInteractiveTuiCommand({
      manager: manager as never,
      configDir,
      raw: "/activity",
      pendingLogin: null,
      activitySettings: { filter: "commands", verbosity: "quiet" },
    });

    expect(result.activitySettings).toBeUndefined();
    expect(result.logLines).toEqual(["activity filter=commands verbosity=quiet"]);
    expect(readFileSync(settingsPath, "utf-8")).toBe(before);
  });

  it("supports /activity status as a read-only settings alias", async () => {
    const manager = {};
    const configDir = mkdtempSync(join(tmpdir(), "tui-activity-status-"));
    const settingsPath = join(configDir, TUI_SETTINGS_FILE);
    writeFileSync(
      settingsPath,
      `${JSON.stringify({
        activity: {
          filter: "runtime",
          verbosity: "verbose",
        },
        updatedAt: "2026-04-10T00:00:00.000Z",
      }, null, 2)}\n`,
      "utf-8",
    );
    const before = readFileSync(settingsPath, "utf-8");

    const result = await handleInteractiveTuiCommand({
      manager: manager as never,
      configDir,
      raw: "/activity status",
      pendingLogin: null,
      activitySettings: { filter: "runtime", verbosity: "verbose" },
    });

    expect(result.activitySettings).toBeUndefined();
    expect(result.logLines).toEqual(["activity filter=runtime verbosity=verbose"]);
    expect(readFileSync(settingsPath, "utf-8")).toBe(before);
  });

  it("resets persisted live activity feed settings", async () => {
    const manager = {};
    const configDir = mkdtempSync(join(tmpdir(), "tui-activity-reset-command-"));
    saveTuiActivitySettings(configDir, {
      filter: "commands",
      verbosity: "quiet",
    });

    const result = await handleInteractiveTuiCommand({
      manager: manager as never,
      configDir,
      raw: "/activity reset",
      pendingLogin: null,
      activitySettings: { filter: "commands", verbosity: "quiet" },
    });

    expect(result.activitySettings).toEqual(DEFAULT_TUI_ACTIVITY_SETTINGS);
    expect(result.logLines).toEqual(["activity filter=all verbosity=normal"]);
    expect(existsSync(join(configDir, TUI_SETTINGS_FILE))).toBe(false);
    expect(loadTuiActivitySettings(configDir)).toEqual(DEFAULT_TUI_ACTIVITY_SETTINGS);
  });

  it("rejects unknown live activity feed settings", async () => {
    const manager = {};

    const result = await handleInteractiveTuiCommand({
      manager: manager as never,
      configDir: ".",
      raw: "/activity chatter",
      pendingLogin: null,
      activitySettings: { filter: "all", verbosity: "normal" },
    });

    expect(result.activitySettings).toBeUndefined();
    expect(result.logLines).toEqual([
      "usage: /activity [status|reset|<all|runtime|prompts|commands|children|errors> [quiet|normal|verbose]]",
    ]);
  });
});
