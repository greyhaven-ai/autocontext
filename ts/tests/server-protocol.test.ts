/**
 * Tests for AC-347: Interactive Server — Protocol types, Run Manager, WS Server.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, readFileSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-server-"));
}

async function waitForCondition(
  predicate: () => boolean,
  timeoutMs = 5000,
  intervalMs = 25,
): Promise<void> {
  const started = Date.now();
  while (!predicate()) {
    if (Date.now() - started > timeoutMs) {
      throw new Error("Timed out waiting for condition");
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
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

  it("getEnvironmentInfo includes saved custom scenarios without touching the game registry", async () => {
    const customDir = join(dir, "knowledge", "_custom_scenarios", "saved_task");
    mkdirSync(customDir, { recursive: true });
    writeFileSync(join(customDir, "scenario_type.txt"), "agent_task", "utf-8");
    writeFileSync(
      join(customDir, "spec.json"),
      JSON.stringify({
        name: "saved_task",
        taskPrompt: "Summarize API incidents.",
        rubric: "Evaluate incident-summary quality.",
        description: "Custom summary task.",
      }),
      "utf-8",
    );

    const { RunManager } = await import("../src/server/run-manager.js");
    const mgr = new RunManager({
      dbPath: join(dir, "test.db"),
      migrationsDir: join(__dirname, "..", "migrations"),
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    const info = mgr.getEnvironmentInfo();
    expect(info.scenarios.some((scenario) => scenario.name === "saved_task")).toBe(true);
    await expect(mgr.startRun("saved_task", 1)).rejects.toThrow(/only built-in game scenarios/i);
  });

  it("startRun executes saved generated custom scenarios after discovery", async () => {
    const { generateSimulationSource } = await import("../src/scenarios/codegen/simulation-codegen.js");

    const spec = {
      description: "Deploy a small service",
      environment_description: "Test environment",
      initial_state_description: "Nothing deployed",
      success_criteria: ["service deployed"],
      failure_modes: ["timeout"],
      max_steps: 5,
      actions: [
        {
          name: "provision",
          description: "Provision infrastructure",
          parameters: {},
          preconditions: [],
          effects: ["infra_ready"],
        },
        {
          name: "deploy",
          description: "Deploy the service",
          parameters: {},
          preconditions: ["provision"],
          effects: ["service_ready"],
        },
      ],
    };

    const customDir = join(dir, "knowledge", "_custom_scenarios", "saved_sim");
    mkdirSync(customDir, { recursive: true });
    writeFileSync(join(customDir, "scenario_type.txt"), "simulation", "utf-8");
    writeFileSync(
      join(customDir, "spec.json"),
      JSON.stringify({
        name: "saved_sim",
        family: "simulation",
        scenario_type: "simulation",
        ...spec,
      }),
      "utf-8",
    );
    writeFileSync(
      join(customDir, "scenario.js"),
      generateSimulationSource(spec, "saved_sim"),
      "utf-8",
    );

    const { RunManager } = await import("../src/server/run-manager.js");
    const mgr = new RunManager({
      dbPath: join(dir, "test.db"),
      migrationsDir: join(__dirname, "..", "migrations"),
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    const info = mgr.getEnvironmentInfo();
    const savedScenario = info.scenarios.find((scenario) => scenario.name === "saved_sim");
    expect(savedScenario).toBeDefined();
    expect(savedScenario?.description).toContain("runnable via /run");

    const seenEvents: Array<{ event: string; payload: Record<string, unknown> }> = [];
    mgr.subscribeEvents((event, payload) => {
      seenEvents.push({ event, payload });
    });

    const runId = await mgr.startRun("saved_sim", 1);
    expect(runId).toBeTypeOf("string");

    await waitForCondition(() => seenEvents.some((entry) => (
      entry.event === "run_completed" || entry.event === "run_failed"
    )));

    const completed = seenEvents.find((entry) => entry.event === "run_completed");
    const failed = seenEvents.find((entry) => entry.event === "run_failed");
    expect(failed).toBeUndefined();
    expect(completed?.payload.run_id).toBe(runId);
    expect(completed?.payload.best_score).toBe(1);
    expect(mgr.getState().active).toBe(false);
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

  it("startRun rejects registry entries that fail the game-family contract", async () => {
    const { RunManager } = await import("../src/server/run-manager.js");
    const { SCENARIO_REGISTRY } = await import("../src/scenarios/registry.js");

    const scenarioName = "broken_contract";
    const original = SCENARIO_REGISTRY[scenarioName];
    class BrokenScenario {
      readonly name = scenarioName;
    }
    SCENARIO_REGISTRY[scenarioName] = BrokenScenario as never;

    try {
      const mgr = new RunManager({
        dbPath: join(dir, "test.db"),
        migrationsDir: join(__dirname, "..", "migrations"),
        runsRoot: join(dir, "runs"),
        knowledgeRoot: join(dir, "knowledge"),
        providerType: "deterministic",
      });

      await expect(mgr.startRun(scenarioName, 1)).rejects.toThrow(/does not satisfy 'game' contract/i);
    } finally {
      if (original) {
        SCENARIO_REGISTRY[scenarioName] = original;
      } else {
        delete SCENARIO_REGISTRY[scenarioName];
      }
    }
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

  it("creates, revises, confirms, and catalogs custom scenarios through the live server", async () => {
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
      await socket.waitFor((msg) => msg.type === "hello");
      await socket.waitFor((msg) => msg.type === "environments");
      await socket.waitFor((msg) => msg.type === "state");

      socket.send({
        type: "create_scenario",
        description: "Create a custom scenario that tests summarizing technical incident reports.",
      });
      expect((await socket.waitFor((msg) => msg.type === "scenario_generating")).type).toBe("scenario_generating");
      const preview = await socket.waitFor((msg) => msg.type === "scenario_preview");
      expect(preview.name).toBeDefined();
      expect(preview.description).toContain("family");

      socket.send({
        type: "revise_scenario",
        feedback: "Keep it focused on incident triage summaries.",
      });
      expect((await socket.waitFor((msg) => msg.type === "scenario_generating")).type).toBe("scenario_generating");
      const revisedPreview = await socket.waitFor((msg) => msg.type === "scenario_preview");
      expect(revisedPreview.name).toBeDefined();

      socket.send({ type: "confirm_scenario" });
      expect((await socket.waitFor((msg) => msg.type === "ack" && msg.action === "confirm_scenario")).action).toBe("confirm_scenario");
      const ready = await socket.waitFor((msg) => msg.type === "scenario_ready");
      const scenarioDir = join(
        dir,
        "knowledge",
        "_custom_scenarios",
        ready.name as string,
      );
      expect(readFileSync(join(scenarioDir, "scenario_type.txt"), "utf-8").trim()).toBe("agent_task");
      const savedSpec = JSON.parse(readFileSync(join(scenarioDir, "spec.json"), "utf-8")) as Record<string, unknown>;
      expect(savedSpec.taskPrompt).toBeDefined();
      expect(savedSpec.scenario_type).toBe("agent_task");
      expect(mgr.getEnvironmentInfo().scenarios.some((scenario) => scenario.name === ready.name)).toBe(true);
    } finally {
      socket.close();
      await server.stop();
    }
  }, 15000);

  it("applies provider switches to subsequent live chat requests", async () => {
    const previousConfigDir = process.env.AUTOCONTEXT_CONFIG_DIR;
    const configDir = join(dir, "config");
    mkdirSync(configDir, { recursive: true });
    process.env.AUTOCONTEXT_CONFIG_DIR = configDir;

    const { RunManager, InteractiveServer } = await import("../src/server/index.js");
    const mgr = new RunManager({
      dbPath: join(dir, "test.db"),
      migrationsDir: join(__dirname, "..", "migrations"),
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
      providerType: "anthropic",
    });
    const server = new InteractiveServer({ runManager: mgr, port: 0 });
    await server.start();

    const socket = await openSocket(server.url);

    try {
      await socket.waitFor((msg) => msg.type === "hello");
      await socket.waitFor((msg) => msg.type === "environments");
      await socket.waitFor((msg) => msg.type === "state");

      socket.send({ type: "chat_agent", role: "analyst", message: "What changed?" });
      const initialError = await socket.waitFor((msg) => msg.type === "error");
      expect(String(initialError.message)).toContain("ANTHROPIC_API_KEY");

      socket.send({ type: "switch_provider", provider: "deterministic" });
      const authStatus = await socket.waitFor((msg) => msg.type === "auth_status");
      expect(authStatus.provider).toBe("deterministic");
      expect(authStatus.authenticated).toBe(true);
      expect(mgr.getActiveProviderType()).toBe("deterministic");

      socket.send({ type: "chat_agent", role: "analyst", message: "What changed?" });
      const reply = await socket.waitFor((msg) => msg.type === "chat_response");
      expect(String(reply.text)).toContain("## Findings");
    } finally {
      socket.close();
      await server.stop();
      if (previousConfigDir === undefined) {
        delete process.env.AUTOCONTEXT_CONFIG_DIR;
      } else {
        process.env.AUTOCONTEXT_CONFIG_DIR = previousConfigDir;
      }
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
