import { describe, expect, it, vi } from "vitest";

import type { GenerationRole, RoleProviderBundle } from "../src/providers/index.js";
import {
  buildChatAgentUserPrompt,
  executeChatAgentInteraction,
  normalizeChatAgentRole,
} from "../src/server/chat-agent-workflow.js";

describe("chat agent workflow", () => {
  it("normalizes only generation roles used by the control plane", () => {
    expect(normalizeChatAgentRole("analyst")).toBe("analyst");
    expect(normalizeChatAgentRole("coach")).toBe("coach");
    expect(normalizeChatAgentRole("not-a-role")).toBeUndefined();
  });

  it("builds a state-aware operator prompt", () => {
    const prompt = buildChatAgentUserPrompt({
      role: "analyst",
      message: "What changed?",
      state: {
        active: true,
        paused: false,
        runId: "run_1",
        scenario: "grid_ctf",
        generation: 2,
        phase: "gate",
      },
    });

    expect(prompt).toContain("[analyst]");
    expect(prompt).toContain("Run active: yes");
    expect(prompt).toContain("Scenario: grid_ctf");
    expect(prompt).toContain("Generation: 2");
    expect(prompt).toContain("Phase: gate");
    expect(prompt).toContain("Operator message: What changed?");
  });

  it("selects role-specific provider/model when the role is recognized", async () => {
    const complete = vi.fn(async () => ({
      text: "## Findings\n\n- Updated guidance.",
      model: "analyst-model",
      usage: {},
    }));
    const bundle: RoleProviderBundle = {
      defaultProvider: { name: "default", defaultModel: () => "default-model", complete: vi.fn() },
      defaultConfig: { providerType: "deterministic", apiKey: "", baseUrl: "", model: "default-model" },
      roleProviders: {
        analyst: { name: "analyst", defaultModel: () => "analyst-model", complete },
      },
      roleModels: {
        analyst: "analyst-model",
      },
    };

    const text = await executeChatAgentInteraction({
      role: "analyst",
      message: "What changed?",
      state: {
        active: false,
        paused: false,
        runId: null,
        scenario: null,
        generation: null,
        phase: null,
      },
      resolveProviderBundle: () => bundle,
      buildProvider: (role?: GenerationRole) => role ? (bundle.roleProviders[role] ?? bundle.defaultProvider) : bundle.defaultProvider,
    });

    expect(text).toContain("## Findings");
    expect(complete).toHaveBeenCalledWith(expect.objectContaining({
      model: "analyst-model",
      systemPrompt: "",
    }));
  });

  it("falls back to the default model for unknown roles", async () => {
    const complete = vi.fn(async () => ({
      text: "generic reply",
      model: "default-model",
      usage: {},
    }));
    const bundle: RoleProviderBundle = {
      defaultProvider: { name: "default", defaultModel: () => "default-model", complete },
      defaultConfig: { providerType: "deterministic", apiKey: "", baseUrl: "", model: "default-model" },
      roleProviders: {},
      roleModels: {},
    };

    await executeChatAgentInteraction({
      role: "helper",
      message: "What changed?",
      state: {
        active: false,
        paused: false,
        runId: null,
        scenario: null,
        generation: null,
        phase: null,
      },
      resolveProviderBundle: () => bundle,
      buildProvider: () => bundle.defaultProvider,
    });

    expect(complete).toHaveBeenCalledWith(expect.objectContaining({
      model: "default-model",
    }));
  });
});
