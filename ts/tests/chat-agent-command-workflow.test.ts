import { describe, expect, it, vi } from "vitest";

import {
  buildChatResponseMessage,
  executeChatAgentCommand,
} from "../src/server/chat-agent-command-workflow.js";

describe("chat agent command workflow", () => {
  it("builds chat response messages", () => {
    expect(
      buildChatResponseMessage({
        role: "analyst",
        text: "## Findings\n- Issue found",
      }),
    ).toEqual({
      type: "chat_response",
      role: "analyst",
      text: "## Findings\n- Issue found",
    });
  });

  it("delegates chat_agent commands to run manager chatAgent", async () => {
    const runManager = {
      getState: vi.fn(() => ({ runId: "run-before-decode" })),
      chatAgent: vi.fn(async () => "## Findings\n- Issue found"),
    };

    await expect(
      executeChatAgentCommand({
        command: {
          type: "chat_agent",
          role: "analyst",
          message: "What changed?",
        },
        runManager,
      }),
    ).resolves.toEqual([
      {
        type: "chat_response",
        role: "analyst",
        text: "## Findings\n- Issue found",
      },
    ]);

    expect(runManager.chatAgent).toHaveBeenCalledWith(
      "analyst",
      "What changed?",
      [],
      "run-before-decode",
    );
  });

  it("echoes run and command correlation on chat responses", async () => {
    const runManager = {
      getState: vi.fn(() => ({ runId: null })),
      chatAgent: vi.fn(async () => "Correlated response"),
    };

    await expect(
      executeChatAgentCommand({
        command: {
          type: "chat_agent",
          role: "analyst",
          message: "What changed?",
          client_run_id: "client-run-1",
          command_id: "command-chat-1",
        },
        runManager,
      }),
    ).resolves.toEqual([
      {
        type: "chat_response",
        role: "analyst",
        text: "Correlated response",
        client_run_id: "client-run-1",
        command_id: "command-chat-1",
      },
    ]);
  });
});
