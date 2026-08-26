import { afterEach, describe, expect, it, vi } from "vitest";
import { execFile, execFileSync } from "node:child_process";

vi.mock("node:child_process", () => ({
  execFile: vi.fn(),
  execFileSync: vi.fn(),
}));

const execFileMock = vi.mocked(execFile);
const execFileSyncMock = vi.mocked(execFileSync);
const kPromisifyCustom = Symbol.for("nodejs.util.promisify.custom");

describe("subscription-backed CLI runtime provider parity", () => {
  afterEach(() => {
    vi.resetAllMocks();
    vi.resetModules();
    delete process.env.AUTOCONTEXT_AGENT_PROVIDER;
  });

  it("includes Claude CLI and Codex settings with defaults", async () => {
    const { AppSettingsSchema } = await import("../src/config/index.js");
    const settings = AppSettingsSchema.parse({});

    expect(settings.claudeModel).toBe("sonnet");
    expect(settings.claudeFallbackModel).toBe("haiku");
    expect(settings.claudeTools).toBeNull();
    expect(settings.claudePermissionMode).toBe("bypassPermissions");
    expect(settings.claudeSessionPersistence).toBe(false);
    expect(settings.claudeTimeout).toBe(600.0);

    expect(settings.codexModel).toBe("o4-mini");
    expect(settings.codexTimeout).toBe(120.0);
    expect(settings.codexWorkspace).toBe("");
    expect(settings.codexApprovalMode).toBe("full-auto");
    expect(settings.codexQuiet).toBe(false);
  });

  it("supports claude-cli and codex provider types", async () => {
    const { createProvider } = await import("../src/providers/index.js");

    expect(createProvider({ providerType: "claude-cli" }).name).toBe("runtime-bridge");
    expect(createProvider({ providerType: "codex" }).name).toBe("runtime-bridge");
  });

  it("resolves claude-cli and codex providers from env", async () => {
    const { resolveProviderConfig } = await import("../src/providers/index.js");

    process.env.AUTOCONTEXT_AGENT_PROVIDER = "claude-cli";
    expect(resolveProviderConfig().providerType).toBe("claude-cli");

    process.env.AUTOCONTEXT_AGENT_PROVIDER = "codex";
    expect(resolveProviderConfig().providerType).toBe("codex");
  });

  it("createConfiguredProvider threads Claude CLI settings into the live provider", async () => {
    execFileSyncMock.mockImplementation(((command: string) => {
      if (command === "which") {
        return "claude-local\n" as never;
      }
      return "" as never;
    }) as unknown as typeof execFileSync);
    execFileMock.mockImplementation(((
      _file: string,
      _args: readonly string[],
      options: unknown,
      callback?: unknown,
    ) => {
      const cb = (typeof options === "function" ? options : callback) as (
        err: Error | null,
        stdout: string,
        stderr: string,
      ) => void;
      cb(
        null,
        JSON.stringify({
          result: "claude output",
          total_cost_usd: 0.01,
          modelUsage: { sonnet: {} },
        }),
        "",
      );
      return {} as never;
    }) as unknown as typeof execFile);
    const execFileAsyncMock = vi.fn(async () => ({
      stdout: JSON.stringify({
        result: "claude output",
        total_cost_usd: 0.01,
        modelUsage: { sonnet: {} },
      }),
      stderr: "",
    }));
    Object.defineProperty(execFileMock, kPromisifyCustom, {
      configurable: true,
      value: execFileAsyncMock,
    });

    const { createConfiguredProvider } = await import("../src/providers/index.js");
    const { provider } = createConfiguredProvider(
      { providerType: "claude-cli" },
      {
        agentProvider: "claude-cli",
        claudeModel: "sonnet",
        claudeFallbackModel: "haiku",
        claudeTools: "read,edit",
        claudePermissionMode: "acceptEdits",
        claudeSessionPersistence: true,
        claudeTimeout: 33,
      },
    );

    const result = await provider.complete({
      systemPrompt: "system prompt",
      userPrompt: "task prompt",
    });

    expect(result.text).toBe("claude output");
    expect(execFileAsyncMock).toHaveBeenCalledWith(
      "claude-local",
      [
        "-p",
        "--output-format",
        "json",
        "--model",
        "sonnet",
        "--fallback-model",
        "haiku",
        "--tools",
        "read,edit",
        "--permission-mode",
        "acceptEdits",
        "--system-prompt",
        "system prompt",
        "task prompt",
      ],
      expect.objectContaining({
        timeout: 33_000,
        encoding: "utf8",
      }),
    );

    const isolated = provider.createIsolatedProvider?.({ noTools: true });
    expect(isolated).toBeDefined();
    await isolated!.complete({ systemPrompt: "private system", userPrompt: "private prompt" });
    const isolatedArgs = (
      execFileAsyncMock.mock.calls as unknown as Array<[string, string[]]>
    ).at(-1)?.[1];
    expect(isolatedArgs).toEqual([
      "-p",
      "--output-format",
      "json",
      "--model",
      "sonnet",
      "--fallback-model",
      "haiku",
      "--tools",
      "",
      "--safe-mode",
      "--disable-slash-commands",
      "--permission-mode",
      "dontAsk",
      "--no-session-persistence",
      "--system-prompt",
      "private system",
      "private prompt",
    ]);

    const modelOverrideResult = await isolated!.complete({
      systemPrompt: "private system",
      userPrompt: "private model override",
      model: "opus",
    });
    const modelOverrideArgs = (
      execFileAsyncMock.mock.calls as unknown as Array<[string, string[]]>
    ).at(-1)?.[1];
    expect(modelOverrideArgs).toContain("opus");
    expect(modelOverrideArgs).toContain("--safe-mode");
    expect(modelOverrideArgs).toContain("--no-session-persistence");
    expect(modelOverrideResult.model).toBe("opus");
    isolated!.close?.();

    const { ClaudeCLIRuntime } = await import("../src/runtimes/claude-cli.js");
    const restrictedRuntime = new ClaudeCLIRuntime({
      model: "sonnet",
      isolatedNoTools: true,
      sessionId: "00000000-0000-4000-8000-000000000000",
      systemPrompt: "ambient system replacement",
      appendSystemPrompt: "ambient customization",
    });
    await restrictedRuntime.generate({ prompt: "runtime prompt", system: "explicit system" });
    const restrictedArgs = (
      execFileAsyncMock.mock.calls as unknown as Array<[string, string[]]>
    ).at(-1)?.[1];
    expect(restrictedArgs).not.toContain("--session-id");
    expect(restrictedArgs).not.toContain("--append-system-prompt");
    expect(restrictedArgs).toContain("explicit system");

    await restrictedRuntime.generate({ prompt: "runtime prompt without explicit system" });
    const noSystemArgs = (
      execFileAsyncMock.mock.calls as unknown as Array<[string, string[]]>
    ).at(-1)?.[1];
    expect(noSystemArgs).not.toContain("ambient system replacement");
  });

  it("buildRoleProviderBundle threads Codex CLI settings into run providers", async () => {
    execFileSyncMock.mockImplementation(((command: string, args?: readonly string[]) => {
      if (command === "which") {
        return "" as never;
      }
      expect(command).toBe("codex");
      expect(args).toBeDefined();
      return "codex output" as never;
    }) as unknown as typeof execFileSync);

    const { buildRoleProviderBundle } = await import("../src/providers/index.js");
    const bundle = buildRoleProviderBundle({
      agentProvider: "codex",
      codexModel: "o3",
      codexTimeout: 12,
      codexWorkspace: "/tmp/codex-workspace",
      codexApprovalMode: "full-auto",
      codexQuiet: true,
    });

    const result = await bundle.defaultProvider.complete({
      systemPrompt: "",
      userPrompt: "bundle task",
    });

    expect(result.text).toBe("codex output");
    expect(execFileSyncMock).toHaveBeenCalledWith(
      "codex",
      [
        "exec",
        "--model",
        "o3",
        "--full-auto",
        "--quiet",
        "--cd",
        "/tmp/codex-workspace",
        "bundle task",
      ],
      expect.any(Object),
    );

    const isolated = bundle.defaultProvider.createIsolatedProvider?.({ noTools: true });
    await isolated!.complete({ systemPrompt: "", userPrompt: "isolated bundle task" });
    const isolatedArgs = execFileSyncMock.mock.calls
      .filter(([command]) => command === "codex")
      .at(-1)?.[1] as string[];
    expect(isolatedArgs).toContain("--ephemeral");
    expect(isolatedArgs).toContain("project_doc_max_bytes=0");
    expect(isolatedArgs).toContain("shell_tool");
    expect(isolatedArgs).toContain("workspace_dependencies");
    expect(isolatedArgs).not.toContain("--full-auto");
    isolated!.close?.();
  });
});
