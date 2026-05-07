import { describe, expect, it } from "vitest";

import { handleInteractiveTuiCommand } from "../src/tui/commands.js";
import {
  formatTuiCommandHelp,
  planTuiMetaCommand,
} from "../src/tui/meta-command.js";

describe("TUI meta command planner", () => {
  it("plans empty submissions as no-op commands", () => {
    expect(planTuiMetaCommand("   ", { hasPendingLogin: true })).toEqual({
      kind: "empty",
    });
  });

  it("plans exact help commands", () => {
    expect(planTuiMetaCommand("  /help  ", { hasPendingLogin: false })).toEqual({
      kind: "help",
    });
    expect(planTuiMetaCommand("/help now", { hasPendingLogin: false })).toEqual({
      kind: "unhandled",
    });
  });

  it("plans exact exit commands", () => {
    expect(planTuiMetaCommand("/quit", { hasPendingLogin: false })).toEqual({
      kind: "exit",
    });
    expect(planTuiMetaCommand("/exit", { hasPendingLogin: true })).toEqual({
      kind: "exit",
    });
    expect(planTuiMetaCommand("/quitter", { hasPendingLogin: false })).toEqual({
      kind: "unhandled",
    });
  });

  it("only plans cancel while a login prompt is pending", () => {
    expect(planTuiMetaCommand("/cancel", { hasPendingLogin: true })).toEqual({
      kind: "cancelPendingLogin",
    });
    expect(planTuiMetaCommand("/cancel", { hasPendingLogin: false })).toEqual({
      kind: "unhandled",
    });
    expect(planTuiMetaCommand("/cancel now", { hasPendingLogin: true })).toEqual({
      kind: "unhandled",
    });
  });

  it("formats stable command help", () => {
    expect(formatTuiCommandHelp()).toEqual(
      expect.arrayContaining([
        '/solve "plain-language goal"',
        "/activity [status|reset|<all|runtime|prompts|commands|children|errors> [quiet|normal|verbose]]",
        "/quit",
      ]),
    );
  });
});

describe("TUI meta command handler", () => {
  it("preserves pending login state on empty submissions", async () => {
    const pendingLogin = { provider: "anthropic" };

    await expect(handleInteractiveTuiCommand({
      manager: {} as never,
      configDir: ".",
      raw: "   ",
      pendingLogin,
    })).resolves.toEqual({
      logLines: [],
      pendingLogin,
    });
  });

  it("cancels pending login prompts without reaching auth handlers", async () => {
    await expect(handleInteractiveTuiCommand({
      manager: {} as never,
      configDir: ".",
      raw: "/cancel",
      pendingLogin: { provider: "anthropic" },
    })).resolves.toEqual({
      logLines: ["cancelled login prompt"],
      pendingLogin: null,
    });
  });

  it("leaves cancel unhandled when no login prompt is pending", async () => {
    await expect(handleInteractiveTuiCommand({
      manager: {} as never,
      configDir: ".",
      raw: "/cancel",
      pendingLogin: null,
    })).resolves.toEqual({
      logLines: ["unknown command; use /help"],
      pendingLogin: null,
    });
  });
});
