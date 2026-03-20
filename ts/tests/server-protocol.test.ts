/**
 * Tests for AC-347: Interactive Server — Protocol types, Run Manager, WS Server.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-server-"));
}

interface BufferedSocket {
  send: (payload: Record<string, unknown>) => void;
  waitFor: (predicate: (msg: Record<string, unknown>) => boolean, timeoutMs?: number) => Promise<Record<string, unknown>>;
  close: () => void;
}

async function openSocket(url: string): Promise<BufferedSocket> {
  const { WebSocket } = await import("ws");
  const ws = new WebSocket(url);
  const queue: Record<string, unknown>[] = [];
  const waiters: Array<{
    predicate: (msg: Record<string, unknown>) => boolean;
    resolve: (msg: Record<string, unknown>) => void;
    reject: (err: Error) => void;
    timer: ReturnType<typeof setTimeout>;
  }> = [];

  const flush = () => {
    for (let i = 0; i < queue.length; i++) {
      const msg = queue[i]!;
      const waiterIndex = waiters.findIndex((waiter) => waiter.predicate(msg));
      if (waiterIndex !== -1) {
        const [waiter] = waiters.splice(waiterIndex, 1);
        clearTimeout(waiter!.timer);
        queue.splice(i, 1);
        waiter!.resolve(msg);
        i -= 1;
      }
    }
  };

  ws.on("message", (data) => {
    const msg = JSON.parse(data.toString()) as Record<string, unknown>;
    queue.push(msg);
    flush();
  });

  await new Promise<void>((resolve, reject) => {
    ws.once("open", () => resolve());
    ws.once("error", (err) => reject(err));
  });

  return {
    send(payload) {
      ws.send(JSON.stringify(payload));
    },
    waitFor(predicate, timeoutMs = 5000) {
      flush();
      const existing = queue.find(predicate);
      if (existing) {
        queue.splice(queue.indexOf(existing), 1);
        return Promise.resolve(existing);
      }
      return new Promise<Record<string, unknown>>((resolve, reject) => {
        const timer = setTimeout(() => {
          const idx = waiters.findIndex((waiter) => waiter.resolve === resolve);
          if (idx !== -1) {
            waiters.splice(idx, 1);
          }
          reject(new Error(`Timed out waiting for message at ${url}`));
        }, timeoutMs);
        waiters.push({ predicate, resolve, reject, timer });
      });
    },
    close() {
      for (const waiter of waiters.splice(0)) {
        clearTimeout(waiter.timer);
        waiter.reject(new Error("socket closed"));
      }
      ws.close();
    },
  };
}

// ---------------------------------------------------------------------------
// Task 24: WebSocket Protocol Types
// ---------------------------------------------------------------------------

describe("Protocol types", () => {
  it("exports PROTOCOL_VERSION", async () => {
    const { PROTOCOL_VERSION } = await import("../src/server/protocol.js");
    expect(PROTOCOL_VERSION).toBe(1);
  });

  it("exports server message schemas", async () => {
    const mod = await import("../src/server/protocol.js");
    expect(mod.HelloMsgSchema).toBeDefined();
    expect(mod.EventMsgSchema).toBeDefined();
    expect(mod.StateMsgSchema).toBeDefined();
    expect(mod.RunAcceptedMsgSchema).toBeDefined();
    expect(mod.AckMsgSchema).toBeDefined();
    expect(mod.ErrorMsgSchema).toBeDefined();
    expect(mod.EnvironmentsMsgSchema).toBeDefined();
  });

  it("exports client command schemas", async () => {
    const mod = await import("../src/server/protocol.js");
    expect(mod.PauseCmdSchema).toBeDefined();
    expect(mod.ResumeCmdSchema).toBeDefined();
    expect(mod.StartRunCmdSchema).toBeDefined();
    expect(mod.InjectHintCmdSchema).toBeDefined();
    expect(mod.OverrideGateCmdSchema).toBeDefined();
  });

  it("HelloMsg parses correctly", async () => {
    const { HelloMsgSchema } = await import("../src/server/protocol.js");
    const msg = HelloMsgSchema.parse({ type: "hello", protocol_version: 1 });
    expect(msg.type).toBe("hello");
    expect(msg.protocol_version).toBe(1);
  });

  it("StartRunCmd validates scenario and generations", async () => {
    const { StartRunCmdSchema } = await import("../src/server/protocol.js");
    const cmd = StartRunCmdSchema.parse({ type: "start_run", scenario: "grid_ctf", generations: 3 });
    expect(cmd.scenario).toBe("grid_ctf");
    expect(cmd.generations).toBe(3);
  });

  it("parseClientMessage dispatches correctly", async () => {
    const { parseClientMessage } = await import("../src/server/protocol.js");
    const msg = parseClientMessage({ type: "pause" });
    expect(msg.type).toBe("pause");
  });

  it("parseClientMessage throws on invalid type", async () => {
    const { parseClientMessage } = await import("../src/server/protocol.js");
    expect(() => parseClientMessage({ type: "bogus" })).toThrow();
  });

  it("OverrideGateCmd validates decision enum", async () => {
    const { OverrideGateCmdSchema } = await import("../src/server/protocol.js");
    const cmd = OverrideGateCmdSchema.parse({ type: "override_gate", decision: "advance" });
    expect(cmd.decision).toBe("advance");
    expect(() => OverrideGateCmdSchema.parse({ type: "override_gate", decision: "invalid" })).toThrow();
  });
});

// ---------------------------------------------------------------------------
// Task 26: Run Manager
// ---------------------------------------------------------------------------

describe("RunManager", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("should be importable", async () => {
    const { RunManager } = await import("../src/server/run-manager.js");
    expect(RunManager).toBeDefined();
  });

  it("isActive returns false initially", async () => {
    const { RunManager } = await import("../src/server/run-manager.js");
    const mgr = new RunManager({
      dbPath: join(dir, "test.db"),
      migrationsDir: join(__dirname, "..", "migrations"),
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    expect(mgr.isActive).toBe(false);
  });

  it("listScenarios returns registered scenarios", async () => {
    const { RunManager } = await import("../src/server/run-manager.js");
    const mgr = new RunManager({
      dbPath: join(dir, "test.db"),
      migrationsDir: join(__dirname, "..", "migrations"),
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    const scenarios = mgr.listScenarios();
    expect(scenarios).toContain("grid_ctf");
  });

  it("getEnvironmentInfo returns scenarios and executor info", async () => {
    const { RunManager } = await import("../src/server/run-manager.js");
    const mgr = new RunManager({
      dbPath: join(dir, "test.db"),
      migrationsDir: join(__dirname, "..", "migrations"),
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    const info = mgr.getEnvironmentInfo();
    expect(info.scenarios.length).toBeGreaterThan(0);
    expect(info.scenarios[0].name).toBe("grid_ctf");
    expect(info.executors.length).toBeGreaterThan(0);
    expect(info.currentExecutor).toBe("local");
  });

  it("startRun returns runId and marks active", async () => {
    const { RunManager } = await import("../src/server/run-manager.js");
    const mgr = new RunManager({
      dbPath: join(dir, "test.db"),
      migrationsDir: join(__dirname, "..", "migrations"),
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
      providerType: "deterministic",
    });
    const runId = await mgr.startRun("grid_ctf", 1);
    expect(runId).toBeDefined();
    expect(typeof runId).toBe("string");
    // Wait for run to complete (deterministic is fast)
    await new Promise(r => setTimeout(r, 500));
  });

  it("startRun throws for unknown scenario", async () => {
    const { RunManager } = await import("../src/server/run-manager.js");
    const mgr = new RunManager({
      dbPath: join(dir, "test.db"),
      migrationsDir: join(__dirname, "..", "migrations"),
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    await expect(mgr.startRun("nonexistent", 1)).rejects.toThrow();
  });

  it("exposes live control surfaces for pause and chat", async () => {
    const { RunManager } = await import("../src/server/run-manager.js");
    const mgr = new RunManager({
      dbPath: join(dir, "test.db"),
      migrationsDir: join(__dirname, "..", "migrations"),
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
      providerType: "deterministic",
    });

    mgr.pause();
    expect(mgr.getState().paused).toBe(true);

    const reply = await mgr.chatAgent("analyst", "What changed?");
    expect(reply).toContain("## Findings");

    mgr.resume();
    expect(mgr.getState().paused).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Task 25: WebSocket server
// ---------------------------------------------------------------------------

describe("InteractiveServer", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("routes interactive commands into the live run and forwards events", async () => {
    const { RunManager, InteractiveServer } = await import("../src/server/index.js");
    const mgr = new RunManager({
      dbPath: join(dir, "test.db"),
      migrationsDir: join(__dirname, "..", "migrations"),
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
      providerType: "deterministic",
    });
    const server = new InteractiveServer({ runManager: mgr, port: 0 });
    await server.start();

    const socket = await openSocket(server.url);

    try {
      expect((await socket.waitFor((msg) => msg.type === "hello")).protocol_version).toBe(1);
      expect((await socket.waitFor((msg) => msg.type === "environments")).type).toBe("environments");
      expect((await socket.waitFor((msg) => msg.type === "state")).paused).toBe(false);

      socket.send({ type: "pause" });
      expect((await socket.waitFor((msg) => msg.type === "state" && msg.paused === true)).paused).toBe(true);
      expect((await socket.waitFor((msg) => msg.type === "ack" && msg.action === "pause")).action).toBe("pause");

      socket.send({ type: "resume" });
      expect((await socket.waitFor((msg) => msg.type === "state" && msg.paused === false)).paused).toBe(false);
      expect((await socket.waitFor((msg) => msg.type === "ack" && msg.action === "resume")).action).toBe("resume");

      socket.send({ type: "inject_hint", text: "Hold the center lane." });
      expect((await socket.waitFor((msg) => msg.type === "ack" && msg.action === "inject_hint")).action).toBe("inject_hint");

      socket.send({ type: "override_gate", decision: "rollback" });
      expect((await socket.waitFor((msg) => msg.type === "ack" && msg.action === "override_gate")).decision).toBe("rollback");

      socket.send({ type: "chat_agent", role: "analyst", message: "What changed?" });
      expect((await socket.waitFor((msg) => msg.type === "chat_response")).text).toContain("## Findings");

      socket.send({ type: "start_run", scenario: "grid_ctf", generations: 1 });
      const accepted = await socket.waitFor((msg) => msg.type === "run_accepted");
      expect(accepted.scenario).toBe("grid_ctf");
      expect((await socket.waitFor((msg) => msg.type === "event" && msg.event === "run_started")).event).toBe("run_started");

      const gateEvent = await socket.waitFor((msg) => msg.type === "event" && msg.event === "gate_decided");
      expect((gateEvent.payload as Record<string, unknown>).decision).toBe("rollback");
      expect((await socket.waitFor((msg) => msg.type === "event" && msg.event === "run_completed")).event).toBe("run_completed");

      const promptPath = join(
        dir,
        "runs",
        accepted.run_id as string,
        "generations",
        "gen_1",
        "competitor_prompt.md",
      );
      expect(readFileSync(promptPath, "utf-8")).toContain("Operator Hint:\nHold the center lane.");
    } finally {
      socket.close();
      await server.stop();
    }
  }, 15000);
});

// ---------------------------------------------------------------------------
// Task 28: CLI tui command
// ---------------------------------------------------------------------------

describe("CLI tui command", () => {
  it("help output includes 'tui' command", async () => {
    const { execFileSync } = await import("node:child_process");
    const result = execFileSync(
      "npx",
      ["tsx", join(__dirname, "..", "src", "cli", "index.ts"), "--help"],
      { encoding: "utf-8", timeout: 10000 },
    );
    expect(result).toContain("tui");
  });
});
