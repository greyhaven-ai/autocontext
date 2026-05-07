import { describe, expect, it, vi } from "vitest";

import { DEFAULT_TUI_ACTIVITY_SETTINGS } from "../src/tui/activity-summary.js";
import {
  executeTuiInteractiveCommandWorkflow,
  type TuiInteractiveCommandEffects,
} from "../src/tui/command-workflow.js";

describe("TUI interactive command workflow", () => {
  it("lets /cancel resolve pending login before API-key submission", async () => {
    const effects = createEffects();

    await expect(executeTuiInteractiveCommandWorkflow({
      raw: "/cancel",
      pendingLogin: { provider: "anthropic" },
      activitySettings: DEFAULT_TUI_ACTIVITY_SETTINGS,
    }, effects)).resolves.toEqual({
      logLines: ["cancelled login prompt"],
      pendingLogin: null,
    });

    expect(effects.pendingLogin.login).not.toHaveBeenCalled();
    expect(effects.authLogin.login).not.toHaveBeenCalled();
  });

  it("submits non-slash input to pending login before normal command routing", async () => {
    const effects = createEffects();

    await expect(executeTuiInteractiveCommandWorkflow({
      raw: "sk-ant-test",
      pendingLogin: { provider: "anthropic", model: "claude" },
      activitySettings: DEFAULT_TUI_ACTIVITY_SETTINGS,
    }, effects)).resolves.toEqual({
      logLines: ["logged in to anthropic"],
      pendingLogin: null,
    });

    expect(effects.pendingLogin.login).toHaveBeenCalledWith(
      "anthropic",
      "sk-ant-test",
      "claude",
      undefined,
    );
    expect(effects.activity.save).not.toHaveBeenCalled();
    expect(effects.chat.chatAgent).not.toHaveBeenCalled();
  });

  it("does not read active run state for unrelated auth commands", async () => {
    const effects = createEffects();

    await expect(executeTuiInteractiveCommandWorkflow({
      raw: "/login anthropic sk-ant-test",
      pendingLogin: null,
      activitySettings: DEFAULT_TUI_ACTIVITY_SETTINGS,
    }, effects)).resolves.toEqual({
      logLines: ["logged in to anthropic"],
      pendingLogin: null,
    });

    expect(effects.readActiveRunId).not.toHaveBeenCalled();
    expect(effects.authLogin.login).toHaveBeenCalledWith(
      "anthropic",
      "sk-ant-test",
      undefined,
      undefined,
    );
  });

  it("routes active-run inspection before chat and auth handling", async () => {
    const effects = createEffects();

    await expect(executeTuiInteractiveCommandWorkflow({
      raw: "/timeline",
      pendingLogin: null,
      activitySettings: DEFAULT_TUI_ACTIVITY_SETTINGS,
    }, effects)).resolves.toEqual({
      logLines: ["timeline line"],
      pendingLogin: null,
    });

    expect(effects.readActiveRunId).toHaveBeenCalledTimes(1);
    expect(effects.runInspection.renderTimeline).toHaveBeenCalledWith("run-active");
    expect(effects.chat.chatAgent).not.toHaveBeenCalled();
    expect(effects.authStatus.readWhoami).not.toHaveBeenCalled();
  });

  it("routes chat before auth handling", async () => {
    const effects = createEffects();

    await expect(executeTuiInteractiveCommandWorkflow({
      raw: "/chat analyst hello",
      pendingLogin: null,
      activitySettings: DEFAULT_TUI_ACTIVITY_SETTINGS,
    }, effects)).resolves.toEqual({
      logLines: ["[analyst] chat response"],
      pendingLogin: null,
    });

    expect(effects.chat.chatAgent).toHaveBeenCalledWith("analyst", "hello");
    expect(effects.authStatus.readWhoami).not.toHaveBeenCalled();
    expect(effects.authLogin.login).not.toHaveBeenCalled();
  });

  it("falls through to the unknown command response without touching adapters", async () => {
    const effects = createEffects();

    await expect(executeTuiInteractiveCommandWorkflow({
      raw: "/not-real",
      pendingLogin: null,
      activitySettings: DEFAULT_TUI_ACTIVITY_SETTINGS,
    }, effects)).resolves.toEqual({
      logLines: ["unknown command; use /help"],
      pendingLogin: null,
    });

    expect(effects.readActiveRunId).not.toHaveBeenCalled();
    expect(effects.activity.save).not.toHaveBeenCalled();
    expect(effects.operator.pause).not.toHaveBeenCalled();
    expect(effects.solve.startRun).not.toHaveBeenCalled();
    expect(effects.startRun.startRun).not.toHaveBeenCalled();
    expect(effects.chat.chatAgent).not.toHaveBeenCalled();
    expect(effects.authLogin.login).not.toHaveBeenCalled();
  });
});

function createEffects(): TuiInteractiveCommandEffects {
  return {
    pendingLogin: {
      login: vi.fn(async (_provider, _apiKey, _model, _baseUrl) => ({
        saved: true,
        provider: "anthropic",
      })),
      selectProvider: vi.fn(() => authStatus("anthropic")),
    },
    activity: {
      reset: vi.fn(() => DEFAULT_TUI_ACTIVITY_SETTINGS),
      save: vi.fn(),
    },
    operator: {
      pause: vi.fn(),
      resume: vi.fn(),
      listScenarios: vi.fn(() => ["grid_ctf"]),
      injectHint: vi.fn(),
      overrideGate: vi.fn(),
    },
    solve: {
      createScenario: vi.fn(async () => ({ name: "scenario" })),
      confirmScenario: vi.fn(async () => ({ name: "scenario" })),
      startRun: vi.fn(async () => "run-solve"),
    },
    startRun: {
      startRun: vi.fn(async () => "run-start"),
    },
    readActiveRunId: vi.fn(() => "run-active"),
    runInspection: {
      renderStatus: vi.fn(async () => ["status line"]),
      renderShow: vi.fn(async () => ["show line"]),
      renderTimeline: vi.fn(async () => ["timeline line"]),
    },
    chat: {
      chatAgent: vi.fn(async () => "chat response\nsecond line"),
    },
    authStatus: {
      selectProvider: vi.fn((provider) => authStatus(provider)),
      readWhoami: vi.fn((provider) => authStatus(provider ?? "anthropic")),
      getActiveProvider: vi.fn(() => "anthropic"),
    },
    authLogout: {
      logout: vi.fn(),
      clearActiveProvider: vi.fn(),
      getActiveProvider: vi.fn(() => "anthropic"),
      selectProvider: vi.fn((provider) => authStatus(provider ?? "anthropic")),
      readWhoami: vi.fn((provider) => authStatus(provider ?? "anthropic")),
    },
    authLogin: {
      providerRequiresKey: vi.fn(() => false),
      login: vi.fn(async (_provider, _apiKey, _model, _baseUrl) => ({
        saved: true,
        provider: "anthropic",
      })),
      selectProvider: vi.fn((provider) => authStatus(provider)),
    },
  };
}

function authStatus(provider: string) {
  return {
    provider,
    authenticated: true,
  };
}
