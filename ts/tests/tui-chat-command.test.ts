import { describe, expect, it, vi } from "vitest";

import {
  executeTuiChatCommandPlan,
  formatTuiChatResponseLine,
  planTuiChatCommand,
} from "../src/tui/chat-command.js";
import { handleInteractiveTuiCommand } from "../src/tui/commands.js";

describe("TUI chat command planner", () => {
  it("plans role and message", () => {
    expect(planTuiChatCommand("/chat analyst What changed?")).toEqual({
      kind: "chat",
      role: "analyst",
      message: "What changed?",
    });
    expect(planTuiChatCommand("  /chat coach   Try a smaller patch  ")).toEqual({
      kind: "chat",
      role: "coach",
      message: "Try a smaller patch",
    });
  });

  it("requires both role and message once /chat has arguments", () => {
    expect(planTuiChatCommand("/chat analyst")).toEqual({
      kind: "usage",
      usageLine: "chat command requires a role and message",
    });
    expect(planTuiChatCommand("/chat   analyst")).toEqual({
      kind: "usage",
      usageLine: "chat command requires a role and message",
    });
  });

  it("leaves bare or similarly prefixed commands unhandled", () => {
    expect(planTuiChatCommand("/chat")).toEqual({ kind: "unhandled" });
    expect(planTuiChatCommand("/chatter analyst hello")).toEqual({ kind: "unhandled" });
  });

  it("formats response first lines", () => {
    expect(formatTuiChatResponseLine("analyst", "first\nsecond")).toBe("[analyst] first");
    expect(formatTuiChatResponseLine("coach", "")).toBe("[coach] ");
  });
});

describe("TUI chat command executor", () => {
  it("routes chats through a narrow command port and formats the first response line", async () => {
    const effects = {
      chatAgent: vi.fn(async () => "First line\nSecond line"),
    };

    await expect(executeTuiChatCommandPlan({
      kind: "chat",
      role: "analyst",
      message: "What changed?",
    }, effects)).resolves.toEqual({
      logLines: ["[analyst] First line"],
    });
    expect(effects.chatAgent).toHaveBeenCalledWith("analyst", "What changed?");
  });

  it("reports usage and ignores unhandled plans without calling the provider", async () => {
    const effects = {
      chatAgent: vi.fn(),
    };

    await expect(executeTuiChatCommandPlan({
      kind: "usage",
      usageLine: "chat command requires a role and message",
    }, effects)).resolves.toEqual({
      logLines: ["chat command requires a role and message"],
    });
    await expect(executeTuiChatCommandPlan({ kind: "unhandled" }, effects)).resolves.toBeNull();
    expect(effects.chatAgent).not.toHaveBeenCalled();
  });

  it("maps provider failures to log lines", async () => {
    const effects = {
      chatAgent: vi.fn(async () => {
        throw new Error("model offline");
      }),
    };

    await expect(executeTuiChatCommandPlan({
      kind: "chat",
      role: "coach",
      message: "Help",
    }, effects)).resolves.toEqual({
      logLines: ["model offline"],
    });
  });
});

describe("TUI chat command handler", () => {
  it("calls chatAgent with planned role and message", async () => {
    const manager = {
      chatAgent: vi.fn(async () => "First line\nSecond line"),
    };

    await expect(
      handleInteractiveTuiCommand({
        manager: manager as never,
        configDir: ".",
        raw: "/chat analyst What changed?",
        pendingLogin: null,
      }),
    ).resolves.toMatchObject({ logLines: ["[analyst] First line"] });
    expect(manager.chatAgent).toHaveBeenCalledWith("analyst", "What changed?");
  });

  it("reports usage before calling chatAgent", async () => {
    const manager = {
      chatAgent: vi.fn(),
    };

    await expect(
      handleInteractiveTuiCommand({
        manager: manager as never,
        configDir: ".",
        raw: "/chat analyst",
        pendingLogin: null,
      }),
    ).resolves.toMatchObject({
      logLines: ["chat command requires a role and message"],
    });
    expect(manager.chatAgent).not.toHaveBeenCalled();
  });
});
