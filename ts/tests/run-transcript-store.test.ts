import { mkdtempSync, readFileSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import type { ClientMessage, ServerMessage } from "../src/server/protocol.js";
import { RunTranscriptStore } from "../src/server/run-transcript-store.js";

const tempDirs: string[] = [];

function makeStore(): { dir: string; path: string; store: RunTranscriptStore } {
  const dir = mkdtempSync(join(tmpdir(), "autoctx-run-transcript-"));
  tempDirs.push(dir);
  const path = join(dir, "_interactive", "run-transcript.ndjson");
  return { dir, path, store: new RunTranscriptStore(path) };
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

describe("RunTranscriptStore", () => {
  it("assigns stable identity and persists presentation-safe exact wire frames", () => {
    const { path, store } = makeStore();
    store.registerRun("client-run-1", "engine-run-1");

    const frame = store.record({
      clientRunId: "client-run-1",
      occurredAt: "2026-07-11T20:00:00.000Z",
      runId: "engine-run-1",
      message: {
        type: "event",
        event: "action_detail",
        payload: {
          run_id: "engine-run-1",
          action_id: "action-1",
          name: "Inspect deployment",
          input: {
            api_key: "super-secret-value",
            apiKeyHeader: "sk-proj_abcdefghijklmnopqrstuvwxyz",
            my_refresh_token_value: "gsk_abcdefghijklmnopqrstuvwxyz",
            google: "AIzaSyDabcdefghijklmnopqrstuvwxyz",
            aws: "AKIAABCDEFGHIJKLMNOP",
            github: "ghp_abcdefghijklmnopqrstuvwxyz123456",
            headers: { Authorization: "Bearer nested-secret" },
            note: "Authorization: Bearer another-secret-value",
            tokens: 42,
          },
          raw_internal_trace: "must-not-be-retained",
        },
      },
    });

    expect(frame).not.toBeNull();
    expect(frame?.sequence).toBe(1);
    expect(frame?.message).toMatchObject({
      client_run_id: "client-run-1",
      occurred_at: "2026-07-11T20:00:00.000Z",
      run_id: "engine-run-1",
      sequence: 1,
      type: "event",
    });
    expect(frame?.eventId).toMatch(/^[0-9a-f-]{36}$/);
    expect(store.framesAfter("client-run-1", 0).map((item) => item.wire)).toEqual([frame?.wire]);

    const payload = frame?.message.type === "event" ? frame.message.payload : {};
    expect(payload.raw_internal_trace).toBeUndefined();
    expect(payload.input).toEqual({
      api_key: "[Redacted]",
      apiKeyHeader: "[Redacted]",
      my_refresh_token_value: "[Redacted]",
      google: "[Redacted]",
      aws: "[Redacted]",
      github: "[Redacted]",
      headers: { Authorization: "[Redacted]" },
      note: "[Redacted]",
      tokens: 42,
    });
    const persisted = readFileSync(path, "utf-8");
    expect(persisted).not.toContain("super-secret-value");
    expect(persisted).not.toContain("another-secret-value");
    expect(persisted).not.toContain("must-not-be-retained");
    for (const secret of [
      "sk-proj_abcdefghijklmnopqrstuvwxyz",
      "gsk_abcdefghijklmnopqrstuvwxyz",
      "AIzaSyDabcdefghijklmnopqrstuvwxyz",
      "AKIAABCDEFGHIJKLMNOP",
      "ghp_abcdefghijklmnopqrstuvwxyz123456",
      "nested-secret",
    ]) {
      expect(persisted).not.toContain(secret);
    }
  });

  it("reloads retained frames after restart and continues monotonic sequencing", () => {
    const { path, store } = makeStore();
    const first = store.record({
      clientRunId: "client-run-1",
      runId: "engine-run-1",
      message: {
        type: "run_accepted",
        run_id: "engine-run-1",
        scenario: "grid_ctf",
        generations: 2,
      },
    });
    const second = store.record({
      clientRunId: "client-run-1",
      runId: "engine-run-1",
      message: { type: "state", paused: false, generation: 1, phase: "agents" },
    });

    const reloaded = new RunTranscriptStore(path);
    expect(reloaded.framesAfter("client-run-1", 0).map((frame) => frame.wire)).toEqual([
      first?.wire,
      second?.wire,
    ]);
    expect(reloaded.resolveRunId("client-run-1")).toBe("engine-run-1");
    expect(reloaded.resolveClientRunId("engine-run-1")).toBe("client-run-1");

    const third = reloaded.record({
      clientRunId: "client-run-1",
      commandId: "command-1",
      message: { type: "ack", action: "pause" },
    });
    expect(third?.sequence).toBe(3);
    expect(third?.message).toMatchObject({ command_id: "command-1", sequence: 3 });
    expect(reloaded.findCommandFrame("client-run-1", "command-1")?.wire).toBe(third?.wire);
    expect(reloaded.framesAfter("client-run-1", 1).map((frame) => frame.sequence)).toEqual([2, 3]);
  });

  it("isolates client-run scopes and refuses conflicting engine attribution", () => {
    const { store } = makeStore();
    const first = store.record({
      clientRunId: "client-run-1",
      runId: "engine-run-1",
      message: { type: "state", paused: false },
    });
    const second = store.record({
      clientRunId: "client-run-2",
      runId: "engine-run-2",
      message: { type: "state", paused: true },
    });

    expect(first?.sequence).toBe(1);
    expect(second?.sequence).toBe(1);
    expect(store.framesAfter("client-run-1", 0)).toHaveLength(1);
    expect(store.framesAfter("client-run-2", 0)).toHaveLength(1);
    expect(() => store.registerRun("client-run-1", "engine-run-2")).toThrow(
      /different engine run|different client_run_id/,
    );
  });

  it("retains unknown run event identity without retaining its raw payload", () => {
    const { store } = makeStore();
    const frame = store.record({
      clientRunId: "client-run-1",
      runId: "engine-run-1",
      message: {
        type: "event",
        event: "raw_model_trace",
        payload: { prompt: "private raw prompt" },
      },
    });
    expect(frame?.message).toMatchObject({
      type: "event",
      client_run_id: "client-run-1",
      run_id: "engine-run-1",
      payload: {},
    });
    expect(frame?.wire).not.toContain("private raw prompt");
    expect(store.hasFrames("client-run-1")).toBe(true);
  });

  it("bounds deeply nested retained records before writing them", () => {
    const { path, store } = makeStore();
    store.record({
      clientRunId: "client-large",
      runId: "engine-large",
      message: {
        type: "event",
        event: "action_detail",
        payload: {
          action_id: "large-action",
          input: Array.from({ length: 2_000 }, (_, index) => ({
            index,
            output: "x".repeat(1_000),
          })),
        },
      },
    });

    for (const line of readFileSync(path, "utf-8").trim().split("\n")) {
      expect(Buffer.byteLength(line, "utf-8")).toBeLessThanOrEqual(32 * 1_024);
    }
  });

  it("journals request-specific command outcomes before replaying exact responses", () => {
    const { path, store } = makeStore();
    const cases: Array<{
      command: ClientMessage;
      changed: ClientMessage;
      response: ServerMessage;
    }> = [
      {
        command: {
          type: "start_run",
          scenario: "grid_ctf",
          generations: 1,
          client_run_id: "client-commands",
          command_id: "command-start",
        },
        changed: {
          type: "start_run",
          scenario: "grid_ctf",
          generations: 2,
          client_run_id: "client-commands",
          command_id: "command-start",
        },
        response: {
          type: "run_accepted",
          run_id: "engine-commands",
          scenario: "grid_ctf",
          generations: 1,
        },
      },
      {
        command: {
          type: "pause",
          client_run_id: "client-commands",
          command_id: "command-control",
        },
        changed: {
          type: "resume",
          client_run_id: "client-commands",
          command_id: "command-control",
        },
        response: { type: "ack", action: "pause" },
      },
      {
        command: {
          type: "chat_agent",
          role: "analyst",
          message: "first question",
          client_run_id: "client-commands",
          command_id: "command-chat",
        },
        changed: {
          type: "chat_agent",
          role: "analyst",
          message: "changed question",
          client_run_id: "client-commands",
          command_id: "command-chat",
        },
        response: { type: "chat_response", role: "analyst", text: "answer" },
      },
      {
        command: {
          type: "resume_run",
          client_run_id: "client-commands",
          after_sequence: 0,
          command_id: "command-resume",
        },
        changed: {
          type: "resume_run",
          client_run_id: "client-commands",
          after_sequence: 1,
          command_id: "command-resume",
        },
        response: { type: "ack", action: "resume_run" },
      },
    ];

    for (const item of cases) {
      const commandId = "command_id" in item.command ? item.command.command_id : undefined;
      if (!commandId) throw new Error("expected command id");
      expect(
        store.beginCommand({
          clientRunId: "client-commands",
          commandId,
          command: item.command,
        }),
      ).toEqual({ outcome: "proceed" });
      const frame = store.record({
        clientRunId: "client-commands",
        commandId,
        message: item.response,
        runId: "engine-commands",
      });
      if (!frame) throw new Error("expected retained response");
      store.completeCommand({
        clientRunId: "client-commands",
        commandId,
        command: item.command,
        frame,
      });
      const reloaded = new RunTranscriptStore(path);
      expect(
        reloaded.beginCommand({
          clientRunId: "client-commands",
          commandId,
          command: item.command,
        }),
      ).toMatchObject({ outcome: "completed", frame: { wire: frame.wire } });
      expect(
        reloaded.beginCommand({
          clientRunId: "client-commands",
          commandId,
          command: item.changed,
        }),
      ).toEqual({ outcome: "conflict" });
    }
  });

  it("fails closed for a pending command after restart", () => {
    const { path, store } = makeStore();
    const command = {
      type: "inject_hint",
      text: "do this once",
      client_run_id: "client-pending",
      command_id: "command-pending",
    } as const;
    expect(
      store.beginCommand({
        clientRunId: "client-pending",
        commandId: "command-pending",
        command,
      }),
    ).toEqual({ outcome: "proceed" });

    expect(
      new RunTranscriptStore(path).beginCommand({
        clientRunId: "client-pending",
        commandId: "command-pending",
        command,
      }),
    ).toEqual({ outcome: "pending" });
    expect(readFileSync(path, "utf-8")).not.toContain("do this once");
  });

  it("prunes by per-run and global limits and compacts atomically", () => {
    const { path } = makeStore();
    const store = new RunTranscriptStore(path, {
      maxFileBytes: 8 * 1_024,
      maxFrames: 3,
      maxFramesPerRun: 2,
    });
    const occurredAt = Date.now();
    for (const [clientRunId, count, offset] of [
      ["client-a", 3, 0],
      ["client-b", 2, 10],
    ] as const) {
      for (let index = 1; index <= count; index += 1) {
        store.record({
          clientRunId,
          occurredAt: new Date(occurredAt + offset + index).toISOString(),
          runId: `engine-${clientRunId}`,
          message: { type: "state", paused: false, generation: index },
        });
      }
    }

    const reloaded = new RunTranscriptStore(path, {
      maxFileBytes: 8 * 1_024,
      maxFrames: 3,
      maxFramesPerRun: 2,
    });
    expect(reloaded.framesAfter("client-a", 0).map((frame) => frame.sequence)).toEqual([3]);
    expect(reloaded.framesAfter("client-b", 0).map((frame) => frame.sequence)).toEqual([1, 2]);
    expect(statSync(path).size).toBeLessThanOrEqual(8 * 1_024);
  });

  it("downgrades a completed command to pending when retention evicts its response", () => {
    const { path } = makeStore();
    const policy = { maxFrames: 1, maxFramesPerRun: 1 };
    const store = new RunTranscriptStore(path, policy);
    const command = {
      type: "pause",
      client_run_id: "client-evicted",
      command_id: "command-evicted",
    } as const;
    store.beginCommand({
      clientRunId: "client-evicted",
      commandId: "command-evicted",
      command,
    });
    const response = store.record({
      clientRunId: "client-evicted",
      commandId: "command-evicted",
      runId: "engine-evicted",
      message: { type: "ack", action: "pause" },
    });
    if (!response) throw new Error("expected retained response");
    store.completeCommand({
      clientRunId: "client-evicted",
      commandId: "command-evicted",
      command,
      frame: response,
    });
    store.record({
      clientRunId: "client-evicted",
      runId: "engine-evicted",
      message: { type: "state", paused: true },
    });

    expect(
      new RunTranscriptStore(path, policy).beginCommand({
        clientRunId: "client-evicted",
        commandId: "command-evicted",
        command,
      }),
    ).toEqual({ outcome: "pending" });
  });
});
