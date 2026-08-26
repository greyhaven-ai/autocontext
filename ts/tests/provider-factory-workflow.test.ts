import { describe, expect, it, vi } from "vitest";

import { SUPPORTED_PROVIDER_TYPES, createProvider } from "../src/providers/provider-factory.js";
import { createInMemoryWorkspaceEnv } from "../src/runtimes/workspace-env.js";
import { RuntimeSession } from "../src/session/runtime-session.js";
import { RuntimeBridgeProvider } from "../src/providers/runtime-bridge.js";

describe("provider factory workflow", () => {
  it("creates compat providers with their family defaults", () => {
    expect(createProvider({ providerType: "gemini", apiKey: "gem-key" }).defaultModel()).toBe(
      "gemini-3.1-pro-preview",
    );
    expect(createProvider({ providerType: "mistral", apiKey: "mistral-key" }).defaultModel()).toBe(
      "mistral-large-2512",
    );
    expect(
      createProvider({ providerType: "openrouter", apiKey: "router-key" }).defaultModel(),
    ).toBe("anthropic/claude-sonnet-5");
  });

  it("creates runtime-backed and renamed provider families", () => {
    expect(createProvider({ providerType: "hermes" }).name).toBe("hermes-gateway");
    expect(createProvider({ providerType: "claude-cli" }).name).toBe("runtime-bridge");
    expect(createProvider({ providerType: "codex" }).name).toBe("runtime-bridge");
    expect(createProvider({ providerType: "pi" }).name).toBe("runtime-bridge");
    expect(createProvider({ providerType: "pi-rpc" }).name).toBe("runtime-bridge");
    expect(createProvider({ providerType: "claude-cli" }).evaluatorIdentity).toBe("claude-cli");
    expect(createProvider({ providerType: "codex" }).evaluatorIdentity).toBe("codex");
    expect(createProvider({ providerType: "pi" }).evaluatorIdentity).toBe("pi");
    expect(createProvider({ providerType: "pi-rpc" }).evaluatorIdentity).toBe("pi-rpc");
  });

  it("accepts runtime session recording options for runtime-backed providers", () => {
    const session = RuntimeSession.create({
      sessionId: "provider-factory-session",
      goal: "record provider calls",
      workspace: createInMemoryWorkspaceEnv({ cwd: "/workspace" }),
    });

    const provider = createProvider({
      providerType: "claude-cli",
      runtimeSession: session,
      runtimeSessionRole: "provider-factory",
      runtimeSessionCwd: "tasks",
    });

    expect(provider.name).toBe("runtime-bridge");
  });

  it("creates a distinct evaluator runtime for persistent pi-rpc providers", () => {
    const provider = createProvider({
      providerType: "pi-rpc",
      piRpcPersistent: true,
      piRpcSessionPersistence: true,
    });

    expect(provider.createIsolatedProvider).toBeTypeOf("function");
    const isolated = provider.createIsolatedProvider!();
    expect(isolated).not.toBe(provider);
    expect(isolated.name).toBe("runtime-bridge");

    isolated.close?.();
    provider.close?.();
  });

  it("fails closed when a bare runtime bridge cannot execute a different model", async () => {
    const generate = vi.fn(async () => ({ text: "must not run" }));
    const provider = new RuntimeBridgeProvider(
      {
        name: "fixed-runtime",
        generate,
        revise: async () => ({ text: "unused" }),
      },
      "configured-model",
    );

    await expect(
      provider.complete({
        systemPrompt: "system",
        userPrompt: "prompt",
        model: "different-model",
      }),
    ).rejects.toThrow(/cannot honor requested model/i);
    expect(generate).not.toHaveBeenCalled();
  });

  it("reports the supported provider surface in unknown-provider errors", () => {
    expect(() => createProvider({ providerType: "bogus" })).toThrow(
      `Supported: ${SUPPORTED_PROVIDER_TYPES.join(", ")}`,
    );
  });
});
