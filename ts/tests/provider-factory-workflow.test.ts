import { describe, expect, it } from "vitest";

import { SUPPORTED_PROVIDER_TYPES, createProvider } from "../src/providers/provider-factory.js";
import { createInMemoryWorkspaceEnv } from "../src/runtimes/workspace-env.js";
import { RuntimeSession } from "../src/session/runtime-session.js";

describe("provider factory workflow", () => {
  it("creates compat providers with their family defaults", () => {
    expect(createProvider({ providerType: "gemini", apiKey: "gem-key" }).defaultModel()).toBe(
      "gemini-2.5-pro",
    );
    expect(createProvider({ providerType: "mistral", apiKey: "mistral-key" }).defaultModel()).toBe(
      "mistral-large-latest",
    );
    expect(
      createProvider({ providerType: "openrouter", apiKey: "router-key" }).defaultModel(),
    ).toBe("anthropic/claude-sonnet-4");
  });

  it("creates runtime-backed and renamed provider families", () => {
    expect(createProvider({ providerType: "hermes" }).name).toBe("hermes-gateway");
    expect(createProvider({ providerType: "claude-cli" }).name).toBe("runtime-bridge");
    expect(createProvider({ providerType: "codex" }).name).toBe("runtime-bridge");
    expect(createProvider({ providerType: "pi" }).name).toBe("runtime-bridge");
    expect(createProvider({ providerType: "pi-rpc" }).name).toBe("runtime-bridge");
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

  it("reports the supported provider surface in unknown-provider errors", () => {
    expect(() => createProvider({ providerType: "bogus" })).toThrow(
      `Supported: ${SUPPORTED_PROVIDER_TYPES.join(", ")}`,
    );
  });
});
