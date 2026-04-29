/**
 * Tests for AC-361: Pi and Pi-RPC provider parity in TypeScript runtime.
 */

import { describe, it, expect, afterEach, vi } from "vitest";
import { EventEmitter } from "node:events";
import { execFile, execFileSync, spawn } from "node:child_process";

vi.mock("node:child_process", () => ({
  execFile: vi.fn(),
  execFileSync: vi.fn(),
  spawn: vi.fn(),
}));

const execFileSyncMock = vi.mocked(execFileSync);
const spawnMock = vi.mocked(spawn);
void execFile;

class FakeStream extends EventEmitter {
  writable = true;
  destroyed = false;
  readonly chunks: string[] = [];

  constructor(private readonly onWrite?: () => void) {
    super();
  }

  setEncoding(_encoding: string): void {}

  write(chunk: string): boolean {
    this.chunks.push(chunk);
    this.onWrite?.();
    return true;
  }

  end(): void {
    this.writable = false;
    this.emit("finish");
  }
}

class InteractiveFakeStream extends EventEmitter {
  writable = true;
  destroyed = false;
  readonly chunks: string[] = [];

  constructor(private readonly onWrite: (chunk: string) => void) {
    super();
  }

  setEncoding(_encoding: string): void {}

  write(chunk: string): boolean {
    this.chunks.push(chunk);
    this.onWrite(chunk);
    return true;
  }

  end(): void {
    this.writable = false;
    this.emit("finish");
  }
}

function createFakeSpawnProcess(stdoutLines: string[], closeCode = 0): {
  child: EventEmitter & {
    stdin: FakeStream;
    stdout: FakeStream;
    stderr: FakeStream;
    killed: boolean;
    exitCode: number | null;
    kill: () => void;
  };
  stdin: FakeStream;
} {
  const child = new EventEmitter() as EventEmitter & {
    stdin: FakeStream;
    stdout: FakeStream;
    stderr: FakeStream;
    killed: boolean;
    exitCode: number | null;
    kill: () => void;
  };
  let emitted = false;
  const emitOutput = (): void => {
    if (emitted) return;
    emitted = true;
    queueMicrotask(() => {
      for (const line of stdoutLines) {
        child.stdout.emit("data", `${line}\n`);
      }
      child.exitCode = closeCode;
      child.emit("close", closeCode);
    });
  };
  const stdin = new FakeStream(emitOutput);
  child.stdin = stdin;
  child.stdout = new FakeStream();
  child.stderr = new FakeStream();
  child.killed = false;
  child.exitCode = null;
  child.kill = vi.fn(() => {
    child.killed = true;
    child.exitCode = -9;
    child.emit("close", -9);
  });

  return { child, stdin };
}

function createInteractiveFakeSpawnProcess(): {
  child: EventEmitter & {
    stdin: InteractiveFakeStream;
    stdout: FakeStream;
    stderr: FakeStream;
    killed: boolean;
    exitCode: number | null;
    kill: () => void;
  };
  stdin: InteractiveFakeStream;
} {
  const child = new EventEmitter() as EventEmitter & {
    stdin: InteractiveFakeStream;
    stdout: FakeStream;
    stderr: FakeStream;
    killed: boolean;
    exitCode: number | null;
    kill: () => void;
  };
  let buffer = "";
  const emitRecord = (record: Record<string, unknown>): void => {
    queueMicrotask(() => {
      child.stdout.emit("data", `${JSON.stringify(record)}\n`);
    });
  };
  const handleCommand = (command: Record<string, unknown>): void => {
    const id = command.id as string | undefined;
    if (command.type === "prompt") {
      emitRecord({ type: "response", command: "prompt", id, success: true });
      emitRecord({
        type: "agent_end",
        messages: [{ role: "assistant", content: `answer:${String(command.message)}` }],
        session_id: "sess-1",
      });
      return;
    }
    if (command.type === "steer") {
      emitRecord({ type: "response", command: "steer", id, success: true, data: { accepted: true } });
      return;
    }
    if (command.type === "follow_up") {
      emitRecord({ type: "response", command: "follow_up", id, success: true, data: { queued: true } });
      return;
    }
    if (command.type === "get_state") {
      emitRecord({ type: "response", command: "get_state", id, success: true, data: { status: "idle", sessionId: "sess-1" } });
      return;
    }
    if (command.type === "get_messages") {
      emitRecord({
        type: "response",
        command: "get_messages",
        id,
        success: true,
        data: { messages: [{ role: "assistant", content: "answer" }] },
      });
      return;
    }
    if (command.type === "abort") {
      emitRecord({ type: "response", command: "abort", id, success: true, data: { aborted: true } });
    }
  };
  const stdin = new InteractiveFakeStream((chunk) => {
    buffer += chunk;
    let newlineIndex = buffer.indexOf("\n");
    while (newlineIndex >= 0) {
      const line = buffer.slice(0, newlineIndex).trim();
      buffer = buffer.slice(newlineIndex + 1);
      if (line) {
        handleCommand(JSON.parse(line) as Record<string, unknown>);
      }
      newlineIndex = buffer.indexOf("\n");
    }
  });
  child.stdin = stdin;
  child.stdout = new FakeStream();
  child.stderr = new FakeStream();
  child.killed = false;
  child.exitCode = null;
  child.kill = vi.fn(() => {
    child.killed = true;
    child.exitCode = -9;
    child.emit("close", -9);
  });

  return { child, stdin };
}

// ---------------------------------------------------------------------------
// Pi config in AppSettingsSchema
// ---------------------------------------------------------------------------

describe("Pi config in AppSettingsSchema", () => {
  it("includes Pi CLI settings with defaults", async () => {
    const { AppSettingsSchema } = await import("../src/config/index.js");
    const settings = AppSettingsSchema.parse({});
    expect(settings.piCommand).toBe("pi");
    expect(settings.piTimeout).toBe(300.0);
    expect(settings.piWorkspace).toBe("");
    expect(settings.piModel).toBe("");
    expect(settings.piNoContextFiles).toBe(false);
  });

  it("includes Pi RPC settings with defaults", async () => {
    const { AppSettingsSchema } = await import("../src/config/index.js");
    const settings = AppSettingsSchema.parse({});
    expect(settings.piRpcEndpoint).toBe("");
    expect(settings.piRpcApiKey).toBe("");
    expect(settings.piRpcSessionPersistence).toBe(true);
    expect(settings.piRpcPersistent).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Pi in createProvider factory
// ---------------------------------------------------------------------------

describe("createProvider Pi support", () => {
  it("supports pi provider type", async () => {
    const { createProvider } = await import("../src/providers/index.js");
    const provider = createProvider({ providerType: "pi" });
    expect(provider.name).toBe("runtime-bridge");
    expect(provider.defaultModel()).toContain("pi");
  });

  it("supports pi-rpc provider type", async () => {
    const { createProvider } = await import("../src/providers/index.js");
    const provider = createProvider({ providerType: "pi-rpc" });
    expect(provider.name).toBe("runtime-bridge");
    expect(provider.defaultModel()).toContain("pi");
  });

  it("error message lists pi and pi-rpc", async () => {
    const { createProvider } = await import("../src/providers/index.js");
    try {
      createProvider({ providerType: "bogus" });
    } catch (err) {
      const msg = (err as Error).message;
      expect(msg).toContain("pi");
      expect(msg).toContain("pi-rpc");
    }
  });
});

// ---------------------------------------------------------------------------
// resolveProviderConfig for Pi
// ---------------------------------------------------------------------------

describe("resolveProviderConfig Pi", () => {
  const saved: Record<string, string | undefined> = {};

  function saveAndClear(): void {
    for (const key of Object.keys(process.env)) {
      if (
        key.startsWith("AUTOCONTEXT_") ||
        key === "ANTHROPIC_API_KEY" ||
        key === "OPENAI_API_KEY"
      ) {
        saved[key] = process.env[key];
        delete process.env[key];
      }
    }
  }

  afterEach(() => {
    for (const key of Object.keys(process.env)) {
      if (
        key.startsWith("AUTOCONTEXT_") ||
        key === "ANTHROPIC_API_KEY" ||
        key === "OPENAI_API_KEY"
      ) {
        if (key in saved) {
          process.env[key] = saved[key];
        } else {
          delete process.env[key];
        }
      }
    }
  });

  it("resolves pi provider from env", async () => {
    saveAndClear();
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "pi";
    const { resolveProviderConfig } = await import("../src/providers/index.js");
    const config = resolveProviderConfig();
    expect(config.providerType).toBe("pi");
  });

  it("resolves pi-rpc provider from env", async () => {
    saveAndClear();
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "pi-rpc";
    const { resolveProviderConfig } = await import("../src/providers/index.js");
    const config = resolveProviderConfig();
    expect(config.providerType).toBe("pi-rpc");
  });
});

// ---------------------------------------------------------------------------
// PiCLI Runtime
// ---------------------------------------------------------------------------

describe("PiCLIRuntime", () => {
  it("is importable", async () => {
    const { PiCLIRuntime } = await import("../src/runtimes/pi-cli.js");
    expect(PiCLIRuntime).toBeDefined();
  });

  it("has correct defaults", async () => {
    const { PiCLIConfig } = await import("../src/runtimes/pi-cli.js");
    const config = new PiCLIConfig();
    expect(config.piCommand).toBe("pi");
    expect(config.timeout).toBe(300.0);
    expect(config.model).toBe("");
  });

  it("parseOutput handles plain text", async () => {
    const { PiCLIRuntime } = await import("../src/runtimes/pi-cli.js");
    const runtime = new PiCLIRuntime();
    const result = runtime.parseOutput("hello from pi");
    expect(result.text).toBe("hello from pi");
  });

  it("parseOutput handles empty", async () => {
    const { PiCLIRuntime } = await import("../src/runtimes/pi-cli.js");
    const runtime = new PiCLIRuntime();
    const result = runtime.parseOutput("");
    expect(result.text).toBe("");
  });

  it("createConfiguredProvider threads Pi CLI settings into the live provider", async () => {
    vi.resetModules();
    execFileSyncMock.mockReturnValue("pi output" as never);

    const { createConfiguredProvider } = await import("../src/providers/index.js");
    const { provider } = createConfiguredProvider(
      { providerType: "pi" },
      {
        agentProvider: "pi",
        piCommand: "pi-local",
        piTimeout: 33,
        piWorkspace: "/tmp/pi-workspace",
        piModel: "pi-checkpoint",
        piNoContextFiles: true,
      },
    );

    const result = await provider.complete({
      systemPrompt: "system prompt",
      userPrompt: "task prompt",
    });

    expect(result.text).toBe("pi output");
    expect(execFileSyncMock).toHaveBeenCalledWith(
      "pi-local",
      ["--print", "--model", "pi-checkpoint", "--no-context-files"],
      expect.objectContaining({
        input: "system prompt\n\ntask prompt",
        timeout: 33_000,
        encoding: "utf-8",
        stdio: ["pipe", "pipe", "pipe"],
        cwd: "/tmp/pi-workspace",
      }),
    );
  });

  it("buildRoleProviderBundle threads Pi CLI settings into run providers", async () => {
    vi.resetModules();
    execFileSyncMock.mockReturnValue("bundle output" as never);

    const { buildRoleProviderBundle } = await import("../src/providers/index.js");
    const bundle = buildRoleProviderBundle({
      agentProvider: "pi",
      piCommand: "pi-bundle",
      piTimeout: 12,
      piWorkspace: "/tmp/pi-bundle-workspace",
      piModel: "pi-bundle-model",
      piNoContextFiles: true,
    });

    const result = await bundle.defaultProvider.complete({
      systemPrompt: "",
      userPrompt: "bundle task",
    });

    expect(result.text).toBe("bundle output");
    expect(execFileSyncMock).toHaveBeenCalledWith(
      "pi-bundle",
      ["--print", "--model", "pi-bundle-model", "--no-context-files"],
      expect.objectContaining({
        input: "bundle task",
        timeout: 12_000,
        cwd: "/tmp/pi-bundle-workspace",
      }),
    );
  });
});

// ---------------------------------------------------------------------------
// PiRPC Runtime
// ---------------------------------------------------------------------------

describe("PiRPCRuntime", () => {
  it("is importable", async () => {
    const { PiRPCRuntime } = await import("../src/runtimes/pi-rpc.js");
    expect(PiRPCRuntime).toBeDefined();
  });

  it("exports persistent Pi RPC runtime", async () => {
    const { PiPersistentRPCRuntime } = await import("../src/runtimes/pi-rpc.js");
    expect(PiPersistentRPCRuntime).toBeDefined();
  });

  it("has correct defaults", async () => {
    const { PiRPCConfig } = await import("../src/runtimes/pi-rpc.js");
    const config = new PiRPCConfig();
    expect(config.piCommand).toBe("pi");
    expect(config.timeout).toBe(120.0);
    expect(config.sessionPersistence).toBe(true);
    expect(config.noContextFiles).toBe(false);
  });

  it("creates isolated sessions per role", async () => {
    const { PiRPCRuntime, PiRPCConfig } = await import("../src/runtimes/pi-rpc.js");
    const rt1 = new PiRPCRuntime(new PiRPCConfig());
    const rt2 = new PiRPCRuntime(new PiRPCConfig());
    // Each runtime instance should have its own session state
    expect(rt1).not.toBe(rt2);
    // Session IDs should differ (or both null initially)
    expect(rt1.currentSessionId).toBeNull();
    expect(rt2.currentSessionId).toBeNull();
  });

  it("createConfiguredProvider uses subprocess Pi RPC JSONL instead of HTTP", async () => {
    vi.resetModules();
    const fakeProcess = createFakeSpawnProcess(
      [
        JSON.stringify({ type: "response", command: "prompt", success: true }),
        JSON.stringify({
          type: "agent_end",
          messages: [{ role: "assistant", content: "first" }],
        }),
      ],
    );
    spawnMock.mockReturnValue(fakeProcess.child as never);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { createConfiguredProvider } = await import("../src/providers/index.js");
    const { provider } = createConfiguredProvider(
      { providerType: "pi-rpc" },
      {
        agentProvider: "pi-rpc",
        piCommand: "pi-rpc-local",
        piTimeout: 45,
        piRpcEndpoint: "http://rpc.local:3284",
        piRpcApiKey: "rpc-key",
        piRpcSessionPersistence: false,
        piNoContextFiles: true,
      },
    );

    const result = await provider.complete({
      systemPrompt: "rpc system",
      userPrompt: "first prompt",
    });

    expect(result.text).toBe("first");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(spawnMock).toHaveBeenCalledWith(
      "pi-rpc-local",
      ["--mode", "rpc", "--no-context-files", "--no-session"],
      expect.objectContaining({
        stdio: ["pipe", "pipe", "pipe"],
      }),
    );

    const input = fakeProcess.stdin.chunks.join("");
    expect(JSON.parse(input.trim())).toMatchObject({
      type: "prompt",
      message: "rpc system\n\nfirst prompt",
    });
    expect(input.endsWith("\n")).toBe(true);
    expect(fakeProcess.stdin.writable).toBe(false);
  });

  it("does not treat a prompt ack as the final assistant output", async () => {
    vi.resetModules();
    const fakeProcess = createFakeSpawnProcess([
      JSON.stringify({ type: "response", command: "prompt", success: true }),
    ]);
    spawnMock.mockReturnValue(fakeProcess.child as never);

    const { PiRPCRuntime, PiRPCConfig } = await import("../src/runtimes/pi-rpc.js");
    const runtime = new PiRPCRuntime(new PiRPCConfig({ piCommand: "pi-rpc-local" }));
    const result = await runtime.generate({ prompt: "first prompt" });

    expect(result.text).toBe("");
    expect(result.metadata?.error).toBe("missing_assistant_response");
  });

  it("pi-rpc uses piModel when no generic model override is set", async () => {
    vi.resetModules();
    const fakeProcess = createFakeSpawnProcess([
      JSON.stringify({
        type: "agent_end",
        messages: [{ role: "assistant", content: "model output" }],
      }),
    ]);
    spawnMock.mockReturnValue(fakeProcess.child as never);

    const { createConfiguredProvider } = await import("../src/providers/index.js");
    const { provider } = createConfiguredProvider(
      { providerType: "pi-rpc" },
      {
        agentProvider: "pi-rpc",
        piCommand: "pi-rpc-local",
        piModel: "manual-pi-model",
      },
    );

    await provider.complete({
      systemPrompt: "",
      userPrompt: "first prompt",
    });

    expect(provider.defaultModel()).toBe("manual-pi-model");
    expect(spawnMock).toHaveBeenCalledWith(
      "pi-rpc-local",
      ["--mode", "rpc", "--model", "manual-pi-model"],
      expect.any(Object),
    );
  });

  it("persistent pi-rpc reuses one subprocess for prompts and live control commands", async () => {
    vi.resetModules();
    const fakeProcess = createInteractiveFakeSpawnProcess();
    spawnMock.mockReturnValue(fakeProcess.child as never);

    const { PiPersistentRPCRuntime, PiRPCConfig } = await import("../src/runtimes/pi-rpc.js");
    const runtime = new PiPersistentRPCRuntime(new PiRPCConfig({ piCommand: "pi-rpc-local" }));

    const first = await runtime.generate({ prompt: "first prompt" });
    const steer = await runtime.steer("prefer shorter answers");
    const followUp = await runtime.followUp("next prompt");
    const state = await runtime.getState();
    const messages = await runtime.getMessages();
    const abort = await runtime.abort();
    const second = await runtime.generate({ prompt: "second prompt" });
    runtime.close();

    expect(first.text).toBe("answer:first prompt");
    expect(first.metadata?.sessionId).toBe("sess-1");
    expect(steer).toEqual(expect.objectContaining({ success: true, accepted: true }));
    expect(followUp).toEqual(expect.objectContaining({ success: true, queued: true }));
    expect(state).toEqual({ status: "idle", sessionId: "sess-1" });
    expect(messages).toEqual([{ role: "assistant", content: "answer" }]);
    expect(abort).toEqual(expect.objectContaining({ success: true, aborted: true }));
    expect(second.text).toBe("answer:second prompt");
    expect(spawnMock).toHaveBeenCalledTimes(1);
    expect(fakeProcess.child.kill).toHaveBeenCalledTimes(1);
  });

  it("createConfiguredProvider uses persistent pi-rpc when configured", async () => {
    vi.resetModules();
    const fakeProcess = createInteractiveFakeSpawnProcess();
    spawnMock.mockReturnValue(fakeProcess.child as never);

    const { createConfiguredProvider } = await import("../src/providers/index.js");
    const { provider } = createConfiguredProvider(
      { providerType: "pi-rpc" },
      {
        agentProvider: "pi-rpc",
        piCommand: "pi-rpc-local",
        piRpcPersistent: true,
      },
    );

    const first = await provider.complete({ systemPrompt: "", userPrompt: "first prompt" });
    const second = await provider.complete({ systemPrompt: "", userPrompt: "second prompt" });

    expect(first.text).toBe("answer:first prompt");
    expect(second.text).toBe("answer:second prompt");
    expect(spawnMock).toHaveBeenCalledTimes(1);
  });
});

afterEach(() => {
  vi.resetAllMocks();
  vi.unstubAllGlobals();
});
