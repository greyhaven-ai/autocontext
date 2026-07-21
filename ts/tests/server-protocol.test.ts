/**
 * Tests for AC-347: Interactive Server — Protocol types, Run Manager, WS Server.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, readFileSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";
import { z } from "zod";

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
  waitFor: (
    predicate: (msg: Record<string, unknown>) => boolean,
    timeoutMs?: number,
  ) => Promise<Record<string, unknown>>;
  close: () => void;
}

const LegacyHelloSchema = z
  .object({ type: z.literal("hello"), protocol_version: z.number().int().optional() })
  .strict();

const LegacyRunMessageSchema = z.discriminatedUnion("type", [
  z
    .object({ type: z.literal("event"), event: z.string(), payload: z.record(z.unknown()) })
    .strict(),
  z
    .object({
      type: z.literal("state"),
      paused: z.boolean(),
      generation: z.number().int().optional(),
      phase: z.string().optional(),
    })
    .strict(),
  z
    .object({
      type: z.literal("run_accepted"),
      run_id: z.string(),
      scenario: z.string(),
      generations: z.number().int(),
    })
    .strict(),
  z
    .object({
      type: z.literal("ack"),
      action: z.string(),
      decision: z.string().optional().nullable(),
    })
    .strict(),
  z.object({ type: z.literal("chat_response"), role: z.string(), text: z.string() }).strict(),
  z.object({ type: z.literal("error"), message: z.string() }).strict(),
  z
    .object({
      type: z.literal("monitor_alert"),
      alert_id: z.string(),
      condition_id: z.string(),
      condition_name: z.string(),
      condition_type: z.string(),
      scope: z.string(),
      detail: z.string(),
    })
    .strict(),
]);

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
    expect(mod.StopCmdSchema).toBeDefined();
    expect(mod.ResumeRunCmdSchema).toBeDefined();
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
    const cmd = StartRunCmdSchema.parse({
      type: "start_run",
      scenario: "grid_ctf",
      generations: 3,
      client_run_id: "client-run-1",
      command_id: "command-start-1",
    });
    expect(cmd.scenario).toBe("grid_ctf");
    expect(cmd.generations).toBe(3);
    expect(cmd.client_run_id).toBe("client-run-1");
    expect(cmd.command_id).toBe("command-start-1");
  });

  it("advertises and validates transcript resume metadata without bumping protocol v1", async () => {
    const { HelloMsgSchema, ResumeRunCmdSchema } = await import("../src/server/protocol.js");
    expect(
      HelloMsgSchema.parse({
        type: "hello",
        protocol_version: 1,
        transcript_protocol_version: 1,
        capabilities: ["run_transcript_v1", "safe_run_stop_v1", "agent_task_plan_v1"],
      }),
    ).toMatchObject({
      protocol_version: 1,
      transcript_protocol_version: 1,
    });
    expect(
      ResumeRunCmdSchema.parse({
        type: "resume_run",
        client_run_id: "client-run-1",
        after_sequence: 7,
        command_id: "command-resume-1",
      }),
    ).toMatchObject({
      after_sequence: 7,
      client_run_id: "client-run-1",
    });
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
    expect(() =>
      OverrideGateCmdSchema.parse({ type: "override_gate", decision: "invalid" }),
    ).toThrow();
  });
});

// ---------------------------------------------------------------------------
// Task 26: Run Manager
// ---------------------------------------------------------------------------

describe("RunManager", () => {
  let dir: string;
  let previousAgentProvider: string | undefined;

  beforeEach(() => {
    dir = makeTempDir();
    // RunManager's providerType: "deterministic" only sets the top-level default provider;
    // buildRoleProviderBundle() routes each generation role off settings.agentProvider,
    // which reads AUTOCONTEXT_AGENT_PROVIDER. Without this, role routing falls back to the
    // "anthropic" schema default and these tests require a real ANTHROPIC_API_KEY.
    previousAgentProvider = process.env.AUTOCONTEXT_AGENT_PROVIDER;
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "deterministic";
  });
  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
    if (previousAgentProvider === undefined) {
      delete process.env.AUTOCONTEXT_AGENT_PROVIDER;
    } else {
      process.env.AUTOCONTEXT_AGENT_PROVIDER = previousAgentProvider;
    }
  });

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
      providerType: "deterministic",
    });
    const info = mgr.getEnvironmentInfo();
    expect(info.scenarios.some((scenario) => scenario.name === "saved_task")).toBe(true);
    const savedTask = info.scenarios.find((scenario) => scenario.name === "saved_task");
    expect(savedTask?.description).toContain("runnable via /run");

    const seenEvents: Array<{ event: string; payload: Record<string, unknown> }> = [];
    mgr.subscribeEvents((event, payload) => {
      seenEvents.push({ event, payload });
    });

    const runId = await mgr.startRun("saved_task", 1);
    await waitForCondition(() =>
      seenEvents.some((entry) => entry.event === "run_completed" || entry.event === "run_failed"),
    );

    const failed = seenEvents.find((entry) => entry.event === "run_failed");
    const completed = seenEvents.find((entry) => entry.event === "run_completed");
    expect(failed).toBeUndefined();
    expect(completed?.payload.run_id).toBe(runId);
    expect(completed?.payload.family).toBe("agent_task");
  });

  it("startRun executes saved generated custom scenarios after discovery", async () => {
    const { generateSimulationSource } =
      await import("../src/scenarios/codegen/simulation-codegen.js");

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

    await waitForCondition(() =>
      seenEvents.some((entry) => entry.event === "run_completed" || entry.event === "run_failed"),
    );

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
    await new Promise((r) => setTimeout(r, 500));
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

      await expect(mgr.startRun(scenarioName, 1)).rejects.toThrow(
        /does not satisfy 'game' contract/i,
      );
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
  let previousAgentProvider: string | undefined;

  beforeEach(() => {
    dir = makeTempDir();
    // See the matching comment in the RunManager describe block above: role-provider
    // routing needs AUTOCONTEXT_AGENT_PROVIDER set, not just providerType: "deterministic".
    previousAgentProvider = process.env.AUTOCONTEXT_AGENT_PROVIDER;
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "deterministic";
  });
  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
    if (previousAgentProvider === undefined) {
      delete process.env.AUTOCONTEXT_AGENT_PROVIDER;
    } else {
      process.env.AUTOCONTEXT_AGENT_PROVIDER = previousAgentProvider;
    }
  });

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
      const hello = await socket.waitFor((msg) => msg.type === "hello");
      expect(LegacyHelloSchema.parse(hello).protocol_version).toBe(1);
      expect((await socket.waitFor((msg) => msg.type === "environments")).type).toBe(
        "environments",
      );
      const initialState = await socket.waitFor((msg) => msg.type === "state");
      expect(LegacyRunMessageSchema.parse(initialState)).toMatchObject({ paused: false });

      socket.send({ type: "pause" });
      expect(
        LegacyRunMessageSchema.parse(
          await socket.waitFor((msg) => msg.type === "state" && msg.paused === true),
        ),
      ).toMatchObject({ paused: true });
      expect(
        LegacyRunMessageSchema.parse(
          await socket.waitFor((msg) => msg.type === "ack" && msg.action === "pause"),
        ),
      ).toMatchObject({ action: "pause" });

      socket.send({ type: "resume" });
      LegacyRunMessageSchema.parse(
        await socket.waitFor((msg) => msg.type === "state" && msg.paused === false),
      );
      LegacyRunMessageSchema.parse(
        await socket.waitFor((msg) => msg.type === "ack" && msg.action === "resume"),
      );

      socket.send({ type: "inject_hint", text: "Hold the center lane." });
      LegacyRunMessageSchema.parse(
        await socket.waitFor((msg) => msg.type === "ack" && msg.action === "inject_hint"),
      );

      socket.send({ type: "override_gate", decision: "rollback" });
      expect(
        LegacyRunMessageSchema.parse(
          await socket.waitFor((msg) => msg.type === "ack" && msg.action === "override_gate"),
        ),
      ).toMatchObject({ decision: "rollback" });

      socket.send({ type: "chat_agent", role: "analyst", message: "What changed?" });
      const chatResponse = LegacyRunMessageSchema.parse(
        await socket.waitFor((msg) => msg.type === "chat_response"),
      );
      expect(chatResponse.type === "chat_response" ? chatResponse.text : "").toContain(
        "## Findings",
      );

      socket.send({ type: "start_run", scenario: "grid_ctf", generations: 1 });
      const accepted = LegacyRunMessageSchema.parse(
        await socket.waitFor((msg) => msg.type === "run_accepted"),
      );
      if (accepted.type !== "run_accepted") throw new Error("expected run acceptance");
      expect(accepted.scenario).toBe("grid_ctf");
      LegacyRunMessageSchema.parse(
        await socket.waitFor((msg) => msg.type === "event" && msg.event === "run_started"),
      );

      const gateEvent = LegacyRunMessageSchema.parse(
        await socket.waitFor((msg) => msg.type === "event" && msg.event === "gate_decided"),
      );
      if (gateEvent.type !== "event") throw new Error("expected gate event");
      expect((gateEvent.payload as Record<string, unknown>).decision).toBe("rollback");
      LegacyRunMessageSchema.parse(
        await socket.waitFor((msg) => msg.type === "event" && msg.event === "run_completed"),
      );

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

  it("stops a paused run once and promotes command retries to the retained terminal receipt", async () => {
    const customDir = join(dir, "knowledge", "_custom_scenarios", "saved_stop_task");
    mkdirSync(customDir, { recursive: true });
    writeFileSync(join(customDir, "scenario_type.txt"), "agent_task", "utf-8");
    writeFileSync(
      join(customDir, "spec.json"),
      JSON.stringify({
        name: "saved_stop_task",
        taskPrompt: "Summarize incidents.",
        rubric: "Evaluate summary quality.",
        description: "A stoppable custom task.",
      }),
      "utf-8",
    );

    const { RunManager, InteractiveServer } = await import("../src/server/index.js");
    const { DeterministicProvider } = await import("../src/providers/deterministic.js");
    const deterministicProvider = new DeterministicProvider();
    let releaseProviderGate: (() => void) | null = null;
    const providerGate = new Promise<void>((resolve) => {
      releaseProviderGate = resolve;
    });
    let providerGatePending = true;
    const gatedProvider = {
      name: deterministicProvider.name,
      defaultModel: () => deterministicProvider.defaultModel(),
      complete: async (opts: Parameters<DeterministicProvider["complete"]>[0]) => {
        if (providerGatePending) {
          providerGatePending = false;
          await providerGate;
        }
        return deterministicProvider.complete(opts);
      },
    };
    const managerOpts = {
      dbPath: join(dir, "test.db"),
      migrationsDir: join(__dirname, "..", "migrations"),
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
      providerType: "deterministic",
      deps: {
        resolveProviderBundle: () => ({
          defaultProvider: gatedProvider,
          defaultConfig: {
            providerType: "deterministic",
            apiKey: "",
            baseUrl: "",
            model: "deterministic-dev",
          },
          roleProviders: {},
          roleModels: {},
        }),
      },
    };
    const mgr = new RunManager(managerOpts);
    const seenEvents: Array<{ event: string; payload: Record<string, unknown> }> = [];
    mgr.subscribeEvents((event, payload) => {
      seenEvents.push({ event, payload });
    });
    mgr.pause();

    const server = new InteractiveServer({ runManager: mgr, port: 0 });
    await server.start();
    const socket = await openSocket(`${server.url}?transcript_protocol_version=1`);
    let terminalReceipt: Record<string, unknown> | null = null;

    try {
      await socket.waitFor((msg) => msg.type === "hello");
      await socket.waitFor((msg) => msg.type === "environments");
      await socket.waitFor((msg) => msg.type === "state");

      socket.send({
        type: "start_run",
        scenario: "saved_stop_task",
        generations: 3,
        client_run_id: "client-stop-run",
        command_id: "command-start-stop-run",
      });
      const accepted = await socket.waitFor((msg) => msg.type === "run_accepted");
      await socket.waitFor((msg) => msg.type === "event" && msg.event === "run_started");

      socket.send({
        type: "stop",
        client_run_id: "stale-client-run",
        command_id: "command-stale-stop",
      });
      expect(
        await socket.waitFor(
          (msg) => msg.type === "error" && msg.command_id === "command-stale-stop",
        ),
      ).toMatchObject({
        client_run_id: "stale-client-run",
        message: expect.stringContaining("does not match"),
      });
      expect(mgr.getState().active).toBe(true);
      mgr.events.emit("future_run_checkpoint", {
        run_id: accepted.run_id,
      });
      expect(
        await socket.waitFor(
          (msg) => msg.type === "event" && msg.event === "future_run_checkpoint",
        ),
      ).toMatchObject({
        client_run_id: "client-stop-run",
      });

      const stopCommand = {
        type: "stop",
        client_run_id: "client-stop-run",
        command_id: "command-stop-run",
      };
      socket.send(stopCommand);
      const acknowledgement = await socket.waitFor(
        (msg) => msg.type === "ack" && msg.command_id === "command-stop-run",
      );
      const terminal = await socket.waitFor(
        (msg) => msg.type === "event" && msg.event === "run_stopped",
      );
      terminalReceipt = terminal;

      expect(acknowledgement).toMatchObject({
        action: "stop",
        client_run_id: "client-stop-run",
        command_id: "command-stop-run",
        decision: "requested",
        run_id: accepted.run_id,
      });
      expect(terminal).toMatchObject({
        client_run_id: "client-stop-run",
        run_id: accepted.run_id,
        payload: {
          command_id: "command-stop-run",
          completed_generations: 0,
          reason: "operator",
          run_id: accepted.run_id,
        },
      });
      expect(Number(acknowledgement.sequence)).toBeLessThan(Number(terminal.sequence));
      await waitForCondition(() => !mgr.getState().active);
      expect(mgr.stop(String(accepted.run_id), "command-after-terminal")).toBe(
        "already_terminal",
      );
      expect(seenEvents.filter((entry) => entry.event === "run_stopped")).toHaveLength(1);
      expect(
        seenEvents.some(
          (entry) => entry.event === "run_completed" || entry.event === "run_failed",
        ),
      ).toBe(false);

      socket.send(stopCommand);
      expect(
        await socket.waitFor((msg) => msg.event_id === terminal.event_id),
      ).toEqual(terminal);
      expect(seenEvents.filter((entry) => entry.event === "run_stopped")).toHaveLength(1);

      socket.send({
        type: "resume_run",
        client_run_id: "client-stop-run",
        after_sequence: 0,
        command_id: "command-replay-stop-run",
      });
      expect(
        await socket.waitFor((msg) => msg.event_id === acknowledgement.event_id),
      ).toEqual(acknowledgement);
      expect(
        await socket.waitFor((msg) => msg.event_id === terminal.event_id),
      ).toEqual(terminal);

      socket.send({
        type: "start_run",
        scenario: "saved_stop_task",
        generations: 1,
        client_run_id: "client-stop-run-2",
        command_id: "command-start-stop-run-2",
      });
      const secondAccepted = await socket.waitFor(
        (msg) => msg.type === "run_accepted" && msg.client_run_id === "client-stop-run-2",
      );
      await socket.waitFor(
        (msg) =>
          msg.type === "event" &&
          msg.event === "run_started" &&
          msg.client_run_id === "client-stop-run-2",
      );

      socket.send(stopCommand);
      expect(
        await socket.waitFor((msg) => msg.event_id === terminal.event_id),
      ).toEqual(terminal);
      mgr.events.emit("future_run_checkpoint", {
        run_id: secondAccepted.run_id,
      });
      expect(
        await socket.waitFor(
          (msg) =>
            msg.type === "event" &&
            msg.event === "future_run_checkpoint" &&
            msg.run_id === secondAccepted.run_id,
        ),
      ).toMatchObject({
        client_run_id: "client-stop-run-2",
      });
      const release = releaseProviderGate;
      if (!release) throw new Error("expected provider gate resolver");
      release();
      await socket.waitFor(
        (msg) =>
          msg.type === "event" &&
          msg.event === "run_completed" &&
          msg.client_run_id === "client-stop-run-2",
      );
      await waitForCondition(() => !mgr.getState().active);
    } finally {
      socket.close();
      await server.stop();
    }

    if (!terminalReceipt) throw new Error("expected retained stop terminal receipt");
    const restartedManager = new RunManager(managerOpts);
    const restartedServer = new InteractiveServer({
      runManager: restartedManager,
      port: 0,
    });
    await restartedServer.start();
    const restartedSocket = await openSocket(
      `${restartedServer.url}?transcript_protocol_version=1`,
    );
    try {
      await restartedSocket.waitFor((msg) => msg.type === "hello");
      await restartedSocket.waitFor((msg) => msg.type === "environments");
      await restartedSocket.waitFor((msg) => msg.type === "state");
      restartedSocket.send({
        type: "stop",
        client_run_id: "client-stop-run",
        command_id: "command-stop-run",
      });
      expect(
        await restartedSocket.waitFor((msg) => msg.event_id === terminalReceipt.event_id),
      ).toEqual(terminalReceipt);
    } finally {
      restartedSocket.close();
      await restartedServer.stop();
    }
  }, 15000);

  it("retains stable run frames for correlated reconnect and restart backfill", async () => {
    const { RunManager, InteractiveServer } = await import("../src/server/index.js");
    const managerOpts = {
      dbPath: join(dir, "test.db"),
      migrationsDir: join(__dirname, "..", "migrations"),
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
      providerType: "deterministic",
    };
    const firstManager = new RunManager(managerOpts);
    const firstServer = new InteractiveServer({ runManager: firstManager, port: 0 });
    await firstServer.start();

    const transcriptUrl = `${firstServer.url}?transcript_protocol_version=1`;
    const socket = await openSocket(transcriptUrl);
    const reconnect = await openSocket(transcriptUrl);
    const otherScope = await openSocket(transcriptUrl);
    let pauseAck: Record<string, unknown> | null = null;
    let resumeAck: Record<string, unknown> | null = null;

    try {
      const hello = await socket.waitFor((msg) => msg.type === "hello");
      expect(hello).toMatchObject({
        protocol_version: 1,
        transcript_protocol_version: 1,
        capabilities: ["run_transcript_v1", "safe_run_stop_v1", "agent_task_plan_v1"],
      });
      await socket.waitFor((msg) => msg.type === "environments");
      await socket.waitFor((msg) => msg.type === "state");

      socket.send({
        type: "start_run",
        scenario: "grid_ctf",
        generations: 1,
        client_run_id: "client-run-1",
        command_id: "command-start-1",
      });
      const accepted = await socket.waitFor((msg) => msg.type === "run_accepted");
      const started = await socket.waitFor(
        (msg) => msg.type === "event" && msg.event === "run_started",
      );
      const initialPlan = await socket.waitFor(
        (msg) =>
          msg.type === "event" &&
          msg.event === "task_plan_updated" &&
          (msg.payload as Record<string, unknown>).update_kind === "initial",
      );
      const terminalPlan = await socket.waitFor(
        (msg) =>
          msg.type === "event" &&
          msg.event === "task_plan_updated" &&
          (msg.payload as Record<string, unknown>).active_step_id === null,
      );
      const completed = await socket.waitFor(
        (msg) => msg.type === "event" && msg.event === "run_completed",
      );
      expect(initialPlan.payload).toMatchObject({
        run_id: accepted.run_id,
        version: 1,
        plan_revision: 1,
        update_kind: "initial",
      });
      expect(terminalPlan.payload).toMatchObject({
        run_id: accepted.run_id,
        active_step_id: null,
      });
      expect(Number(started.sequence)).toBeLessThan(Number(initialPlan.sequence));
      expect(Number(initialPlan.sequence)).toBeLessThan(Number(terminalPlan.sequence));
      expect(Number(terminalPlan.sequence)).toBeLessThan(Number(completed.sequence));
      expect(
        (terminalPlan.payload as { steps: { status: string }[] }).steps.some(
          (step) => step.status === "in_progress",
        ),
      ).toBe(false);
      socket.send({
        type: "stop",
        client_run_id: "client-run-1",
        command_id: "command-stop-completed-run",
      });
      expect(
        await socket.waitFor(
          (msg) => msg.type === "ack" && msg.command_id === "command-stop-completed-run",
        ),
      ).toMatchObject({
        action: "stop",
        decision: "already_terminal",
      });
      await expect(
        socket.waitFor((msg) => msg.type === "event" && msg.event === "run_stopped", 150),
      ).rejects.toThrow(/Timed out/);
      firstManager.events.emit(
        "monitor_alert",
        {
          alert_id: "alert-1",
          condition_id: "condition-1",
          condition_name: "Run safety",
          condition_type: "process_exit",
          scope: `run:${String(accepted.run_id)}`,
          detail: "Authorization: Bearer should-not-be-retained",
        },
        "monitor",
      );
      const monitor = await socket.waitFor((msg) => msg.type === "monitor_alert");
      expect(monitor.detail).toBe("[Redacted]");

      firstManager.events.emit("future_run_checkpoint", {
        run_id: accepted.run_id,
        raw_internal_prompt: "must-not-cross-the-wire",
      });
      const futureCheckpoint = await socket.waitFor(
        (msg) => msg.type === "event" && msg.event === "future_run_checkpoint",
      );
      expect(futureCheckpoint).toMatchObject({
        client_run_id: "client-run-1",
        payload: {},
      });
      expect(JSON.stringify(futureCheckpoint)).not.toContain("must-not-cross-the-wire");

      await expect(
        otherScope.waitFor((msg) => msg.client_run_id === "client-run-1", 250),
      ).rejects.toThrow(/Timed out/);

      for (const frame of [
        accepted,
        started,
        initialPlan,
        terminalPlan,
        completed,
        monitor,
        futureCheckpoint,
      ]) {
        expect(frame.client_run_id).toBe("client-run-1");
        expect(frame.run_id).toBe(accepted.run_id);
        expect(frame.event_id).toMatch(/^[0-9a-f-]{36}$/);
        expect(frame.sequence).toEqual(expect.any(Number));
        expect(Number.isFinite(Date.parse(String(frame.occurred_at)))).toBe(true);
      }
      expect(accepted.command_id).toBe("command-start-1");

      socket.send({
        type: "chat_agent",
        role: "analyst",
        message: "What changed?",
        client_run_id: "client-run-1",
        command_id: "command-chat-1",
      });
      const chat = await socket.waitFor(
        (msg) => msg.type === "chat_response" && msg.command_id === "command-chat-1",
      );
      expect(chat.client_run_id).toBe("client-run-1");

      socket.send({
        type: "pause",
        client_run_id: "client-run-1",
        command_id: "command-pause-1",
      });
      pauseAck = await socket.waitFor(
        (msg) => msg.type === "ack" && msg.command_id === "command-pause-1",
      );
      expect(pauseAck.client_run_id).toBe("client-run-1");

      socket.send({
        type: "pause",
        client_run_id: "client-run-1",
        command_id: "command-pause-1",
      });
      expect(
        await socket.waitFor((msg) => msg.type === "ack" && msg.command_id === "command-pause-1"),
      ).toEqual(pauseAck);

      for (const connection of [reconnect, otherScope]) {
        await connection.waitFor((msg) => msg.type === "hello");
        await connection.waitFor((msg) => msg.type === "environments");
        await connection.waitFor((msg) => msg.type === "state");
      }
      reconnect.send({
        type: "resume_run",
        client_run_id: "client-run-1",
        after_sequence: Number(started.sequence),
        command_id: "command-backfill-1",
      });
      expect(await reconnect.waitFor((msg) => msg.event_id === initialPlan.event_id)).toEqual(
        initialPlan,
      );
      expect(await reconnect.waitFor((msg) => msg.event_id === terminalPlan.event_id)).toEqual(
        terminalPlan,
      );
      expect(await reconnect.waitFor((msg) => msg.event_id === chat.event_id)).toEqual(chat);
      expect(await reconnect.waitFor((msg) => msg.event_id === pauseAck.event_id)).toEqual(
        pauseAck,
      );
      resumeAck = await reconnect.waitFor(
        (msg) => msg.type === "ack" && msg.command_id === "command-backfill-1",
      );
      expect(resumeAck).toMatchObject({
        action: "resume_run",
        client_run_id: "client-run-1",
      });

      otherScope.send({
        type: "resume_run",
        client_run_id: "client-run-2",
        after_sequence: 0,
        command_id: "command-backfill-2",
      });
      await otherScope.waitFor(
        (msg) => msg.type === "ack" && msg.command_id === "command-backfill-2",
      );
      socket.send({
        type: "resume",
        client_run_id: "client-run-1",
        command_id: "command-client-1-only",
      });
      await socket.waitFor(
        (msg) => msg.type === "ack" && msg.command_id === "command-client-1-only",
      );
      await expect(
        otherScope.waitFor((msg) => msg.command_id === "command-client-1-only", 250),
      ).rejects.toThrow(/Timed out/);

      socket.send({
        type: "start_run",
        scenario: "grid_ctf",
        generations: 1,
        client_run_id: "client-run-1",
        command_id: "command-start-1",
      });
      expect(await socket.waitFor((msg) => msg.event_id === accepted.event_id)).toEqual(accepted);
      expect(firstManager.getState().active).toBe(false);

      socket.send({
        type: "start_run",
        scenario: "grid_ctf",
        generations: 1,
        client_run_id: "client-run-1",
        command_id: "command-start-conflict",
      });
      expect(
        await socket.waitFor(
          (msg) => msg.type === "error" && msg.command_id === "command-start-conflict",
        ),
      ).toMatchObject({
        client_run_id: "client-run-1",
        message: expect.stringContaining("existing run"),
      });
      expect(firstManager.getState().active).toBe(false);
    } finally {
      socket.close();
      reconnect.close();
      otherScope.close();
      await firstServer.stop();
    }

    if (!pauseAck || !resumeAck) throw new Error("expected retained acknowledgements");

    const secondManager = new RunManager(managerOpts);
    const secondServer = new InteractiveServer({ runManager: secondManager, port: 0 });
    await secondServer.start();
    const restarted = await openSocket(`${secondServer.url}?transcript_protocol_version=1`);
    try {
      await restarted.waitFor((msg) => msg.type === "hello");
      await restarted.waitFor((msg) => msg.type === "environments");
      await restarted.waitFor((msg) => msg.type === "state");
      restarted.send({
        type: "resume_run",
        client_run_id: "client-run-1",
        after_sequence: Number(pauseAck.sequence) - 1,
        command_id: "command-restart-backfill",
      });
      expect(await restarted.waitFor((msg) => msg.event_id === pauseAck.event_id)).toEqual(
        pauseAck,
      );
      const restartAck = await restarted.waitFor(
        (msg) => msg.type === "ack" && msg.command_id === "command-restart-backfill",
      );
      expect(Number(restartAck.sequence)).toBeGreaterThan(Number(resumeAck.sequence));
    } finally {
      restarted.close();
      await secondServer.stop();
    }
  }, 20000);

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
      expect((await socket.waitFor((msg) => msg.type === "scenario_generating")).type).toBe(
        "scenario_generating",
      );
      const preview = await socket.waitFor((msg) => msg.type === "scenario_preview");
      expect(preview.name).toBeDefined();
      expect(preview.description).toContain("family");

      socket.send({
        type: "revise_scenario",
        feedback: "Keep it focused on incident triage summaries.",
      });
      expect((await socket.waitFor((msg) => msg.type === "scenario_generating")).type).toBe(
        "scenario_generating",
      );
      const revisedPreview = await socket.waitFor((msg) => msg.type === "scenario_preview");
      expect(revisedPreview.name).toBeDefined();

      socket.send({ type: "confirm_scenario" });
      expect(
        (await socket.waitFor((msg) => msg.type === "ack" && msg.action === "confirm_scenario"))
          .action,
      ).toBe("confirm_scenario");
      const ready = await socket.waitFor((msg) => msg.type === "scenario_ready");
      const scenarioDir = join(dir, "knowledge", "_custom_scenarios", ready.name as string);
      expect(readFileSync(join(scenarioDir, "scenario_type.txt"), "utf-8").trim()).toBe(
        "agent_task",
      );
      const savedSpec = JSON.parse(readFileSync(join(scenarioDir, "spec.json"), "utf-8")) as Record<
        string,
        unknown
      >;
      expect(savedSpec.taskPrompt).toBeDefined();
      expect(savedSpec.scenario_type).toBe("agent_task");
      expect(
        mgr.getEnvironmentInfo().scenarios.some((scenario) => scenario.name === ready.name),
      ).toBe(true);
    } finally {
      socket.close();
      await server.stop();
    }
  }, 15000);

  // AC-873: routeRoleProvider() now accepts a providerOverride so per-role providers
  // (competitor/analyst/coach/...) track switch_provider the same way the default
  // provider already did.
  it("applies provider switches to subsequent live chat requests", async () => {
    const previousConfigDir = process.env.AUTOCONTEXT_CONFIG_DIR;
    const configDir = join(dir, "config");
    mkdirSync(configDir, { recursive: true });
    process.env.AUTOCONTEXT_CONFIG_DIR = configDir;
    // This test exercises the genuine anthropic-without-key error path, so it needs the
    // opposite of the describe block's beforeEach override (which forces role routing to
    // "deterministic" for every other test here).
    const previousAgentProviderForThisTest = process.env.AUTOCONTEXT_AGENT_PROVIDER;
    delete process.env.AUTOCONTEXT_AGENT_PROVIDER;

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
      if (previousAgentProviderForThisTest === undefined) {
        delete process.env.AUTOCONTEXT_AGENT_PROVIDER;
      } else {
        process.env.AUTOCONTEXT_AGENT_PROVIDER = previousAgentProviderForThisTest;
      }
    }
  }, 15000);

  // AC-873 fix round: the no-pin variant above proved the override reaches per-role
  // routing, but under the common deployment shape (AUTOCONTEXT_AGENT_PROVIDER pinned via
  // env) a live env var used to win for BOTH the default provider and per-role routes,
  // consistently — so switch_provider silently no-opped even though
  // getActiveProviderType()/auth_status reported the switch as successful. This variant
  // pins the env var to "anthropic" (instead of deleting it) to prove a deliberate
  // mid-session switch_provider now wins over that pin.
  it("applies provider switches to subsequent live chat requests even when AUTOCONTEXT_AGENT_PROVIDER is pinned via env", async () => {
    const previousConfigDir = process.env.AUTOCONTEXT_CONFIG_DIR;
    const configDir = join(dir, "config");
    mkdirSync(configDir, { recursive: true });
    process.env.AUTOCONTEXT_CONFIG_DIR = configDir;
    const previousAgentProviderForThisTest = process.env.AUTOCONTEXT_AGENT_PROVIDER;
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "anthropic";

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
      if (previousAgentProviderForThisTest === undefined) {
        delete process.env.AUTOCONTEXT_AGENT_PROVIDER;
      } else {
        process.env.AUTOCONTEXT_AGENT_PROVIDER = previousAgentProviderForThisTest;
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
