/**
 * Tests for agentOS session adapter (AC-517).
 *
 * DDD: AgentOsAdapter is a port adapter that bridges autocontext's
 * Session aggregate to agentOS's VM lifecycle. All tests use a
 * stub AgentOsRuntime so there's no real VM dependency.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";

// ---- Stub agentOS runtime ----

function createStubRuntime(): StubAgentOsRuntime {
  return new StubAgentOsRuntime();
}

class StubAgentOsRuntime {
  sessions = new Map<string, { agentType: string; events: unknown[]; handlers: Array<(e: unknown) => void>; closed: boolean }>();
  promptLog: Array<{ sessionId: string; prompt: string }> = [];
  filesWritten = new Map<string, string>();
  private nextSessionId = 1;

  async createSession(agentType: string, _opts?: Record<string, unknown>): Promise<{ sessionId: string }> {
    const sessionId = `aos-${this.nextSessionId++}`;
    this.sessions.set(sessionId, { agentType, events: [], handlers: [], closed: false });
    return { sessionId };
  }

  async prompt(sessionId: string, prompt: string): Promise<void> {
    this.promptLog.push({ sessionId, prompt });
    const session = this.sessions.get(sessionId);
    if (session) {
      const event = { method: "message", params: { role: "assistant", content: `Response to: ${prompt.slice(0, 50)}` } };
      session.events.push(event);
      for (const h of session.handlers) h(event);
    }
  }

  onSessionEvent(sessionId: string, handler: (event: unknown) => void): void {
    const session = this.sessions.get(sessionId);
    if (session) {
      for (const e of session.events) handler(e);
      session.handlers.push(handler);
    }
  }

  async closeSession(sessionId: string): Promise<void> {
    const session = this.sessions.get(sessionId);
    if (session) session.closed = true;
  }

  async writeFile(path: string, content: string): Promise<void> {
    this.filesWritten.set(path, content);
  }

  async readFile(path: string): Promise<Uint8Array> {
    const content = this.filesWritten.get(path);
    if (!content) throw new Error(`File not found: ${path}`);
    return new TextEncoder().encode(content);
  }

  async dispose(): Promise<void> {}
}

// ---- Tests ----

import type { AgentOsRuntimePort } from "../src/agentos/types.js";
import { AgentOsConfig, AgentOsPermissions } from "../src/agentos/types.js";
import { AgentOsSessionAdapter } from "../src/agentos/adapter.js";
import { AgentOsLifecycle } from "../src/agentos/lifecycle.js";

describe("AgentOsConfig", () => {
  it("creates with defaults", () => {
    const config = new AgentOsConfig();
    expect(config.agentType).toBe("pi");
    expect(config.enabled).toBe(false);
    expect(config.permissions.network).toBe(false);
  });

  it("accepts overrides", () => {
    const config = new AgentOsConfig({
      enabled: true,
      agentType: "claude-code",
      workspacePath: "/home/user/project",
      permissions: new AgentOsPermissions({ network: true, filesystem: "readwrite" }),
    });
    expect(config.agentType).toBe("claude-code");
    expect(config.enabled).toBe(true);
    expect(config.permissions.network).toBe(true);
    expect(config.permissions.filesystem).toBe("readwrite");
  });
});

describe("AgentOsSessionAdapter", () => {
  let runtime: StubAgentOsRuntime;
  let adapter: AgentOsSessionAdapter;

  beforeEach(() => {
    runtime = createStubRuntime();
    adapter = new AgentOsSessionAdapter(runtime as unknown as AgentOsRuntimePort, new AgentOsConfig({ enabled: true }));
  });

  it("starts a session", async () => {
    const session = await adapter.startSession("Build auth API");
    expect(session.sessionId).toBeTruthy();
    expect(session.goal).toBe("Build auth API");
    expect(runtime.sessions.size).toBe(1);
  });

  it("submits a turn via prompt", async () => {
    const session = await adapter.startSession("test");
    await adapter.submitTurn(session.sessionId, "Write a hello world script");
    expect(runtime.promptLog).toHaveLength(1);
    expect(runtime.promptLog[0].prompt).toContain("hello world");
    expect(session.turns).toHaveLength(1);
  });

  it("completes a turn with response", async () => {
    const session = await adapter.startSession("test");
    const result = await adapter.submitTurn(session.sessionId, "What is 2+2?");
    expect(result.response).toBeTruthy();
    expect(result.outcome).toBe("completed");
  });

  it("closes session", async () => {
    const session = await adapter.startSession("test");
    await adapter.closeSession(session.sessionId);
    expect(session.status).toBe("completed");
    const aosSession = [...runtime.sessions.values()][0];
    expect(aosSession.closed).toBe(true);
  });

  it("emits events to session", async () => {
    const session = await adapter.startSession("test");
    await adapter.submitTurn(session.sessionId, "do something");
    expect(session.events.length).toBeGreaterThanOrEqual(2); // session_created + turn_submitted
  });

  it("rejects turn on closed session", async () => {
    const session = await adapter.startSession("test");
    await adapter.closeSession(session.sessionId);
    await expect(adapter.submitTurn(session.sessionId, "too late")).rejects.toThrow();
  });

  it("tracks multiple sessions", async () => {
    const s1 = await adapter.startSession("goal-a");
    const s2 = await adapter.startSession("goal-b");
    expect(adapter.activeSessions).toHaveLength(2);
    await adapter.closeSession(s1.sessionId);
    expect(adapter.activeSessions).toHaveLength(1);
  });
});

describe("AgentOsLifecycle", () => {
  let runtime: StubAgentOsRuntime;

  beforeEach(() => {
    runtime = createStubRuntime();
  });

  it("mount workspace writes to VM", async () => {
    const lifecycle = new AgentOsLifecycle(runtime as unknown as AgentOsRuntimePort);
    await lifecycle.mountWorkspace("/home/user/project");
    // Lifecycle should have recorded the mount
    expect(lifecycle.mountedPaths).toContain("/home/user/project");
  });

  it("shutdown disposes runtime", async () => {
    const lifecycle = new AgentOsLifecycle(runtime as unknown as AgentOsRuntimePort);
    await lifecycle.startSession("test-session", "pi");
    await lifecycle.shutdown();
    expect(lifecycle.isShutdown).toBe(true);
  });

  it("escalation check identifies sandbox needs", () => {
    const lifecycle = new AgentOsLifecycle(runtime as unknown as AgentOsRuntimePort);
    expect(lifecycle.needsSandbox("Run browser tests with Playwright")).toBe(true);
    expect(lifecycle.needsSandbox("Write a utility function")).toBe(false);
    expect(lifecycle.needsSandbox("Start a dev server on port 3000")).toBe(true);
  });
});
