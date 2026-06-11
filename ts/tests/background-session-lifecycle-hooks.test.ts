import { describe, expect, it } from "vitest";

import {
  buildLifecycleHookEnv,
  executeBackgroundSessionLifecycleHooks,
  executeLifecycleHook,
  type LifecycleHookInvocation,
  type LifecycleHookRunner,
} from "../src/session/background-session-lifecycle-hooks.js";

const context = {
  session_id: "run:run-123:runtime",
  run_id: "run-123",
  task_id: "task-123",
  worker_id: "worker-1",
};
const timestamp = "2026-06-01T00:05:00.000Z";

describe("background session lifecycle hook contracts", () => {
  it("skips absent hooks without invoking an adapter", async () => {
    const invocations: LifecycleHookInvocation[] = [];
    const result = await executeLifecycleHook({
      hook: "setup",
      context,
      sequence: 20,
      timestamp,
      runner: async (invocation) => {
        invocations.push(invocation);
        return { exit_code: 0 };
      },
    });

    expect(invocations).toEqual([]);
    expect(result.outcome).toMatchObject({
      hook: "setup",
      phase: "skipped",
      ok: true,
      terminal: false,
      failure_policy: "continue",
    });
    expect(result.events).toEqual([
      {
        event_id: "lifecycle:run:run-123:runtime:setup:skipped:20",
        session_id: "run:run-123:runtime",
        sequence: 20,
        ts: timestamp,
        event: "session_status",
        source_event_type: "lifecycle_hook",
        status: "skipped",
        title: "Lifecycle hook setup skipped",
        payload_summary: { hook: "setup", phase: "skipped" },
      },
    ]);
    expect(result.next_sequence).toBe(21);
  });

  it("runs successful setup and start hooks in order with explicit AUTOCTX env", async () => {
    const invocations: LifecycleHookInvocation[] = [];
    const runner: LifecycleHookRunner = async (invocation) => {
      invocations.push(invocation);
      return { exit_code: 0, stdout: "ok", stderr: "" };
    };

    const result = await executeBackgroundSessionLifecycleHooks({
      hooks: {
        setup: {
          command: ["npm", "install"],
          timeout_ms: 30_000,
          env: { AUTOCTX_HOOK_MODE: "bootstrap", SECRET_TOKEN: "explicit-secret" },
        },
        start: { command: ["autoctx", "run"], cwd: "/workspace" },
      },
      context,
      sequence: 30,
      timestamp,
      runner,
    });

    expect(result.outcomes.map((outcome) => [outcome.hook, outcome.phase, outcome.ok])).toEqual([
      ["setup", "completed", true],
      ["start", "completed", true],
    ]);
    expect(invocations.map((invocation) => invocation.hook)).toEqual(["setup", "start"]);
    expect(invocations[0]?.env).toEqual({
      AUTOCTX_BACKGROUND_SESSION_ID: "run:run-123:runtime",
      AUTOCTX_SESSION_ID: "run:run-123:runtime",
      AUTOCTX_RUN_ID: "run-123",
      AUTOCTX_TASK_ID: "task-123",
      AUTOCTX_WORKER_ID: "worker-1",
      AUTOCTX_HOOK_NAME: "setup",
      AUTOCTX_HOOK_MODE: "bootstrap",
      SECRET_TOKEN: "explicit-secret",
    });
    expect(invocations[1]?.cwd).toBe("/workspace");
    expect(result.terminal).toBe(false);
    expect(
      result.events.map((event) => [event.payload_summary.hook, event.payload_summary.phase]),
    ).toEqual([
      ["setup", "started"],
      ["setup", "completed"],
      ["start", "started"],
      ["start", "completed"],
    ]);
    expect(JSON.stringify(result.events)).not.toContain("explicit-secret");
  });

  it("records setup timeouts as non-terminal when the policy allows continuation", async () => {
    const result = await executeBackgroundSessionLifecycleHooks({
      hooks: {
        setup: { command: ["./bootstrap"], timeout_ms: 10, failure_policy: "continue" },
        start: { command: ["autoctx", "run"] },
      },
      context,
      sequence: 40,
      timestamp,
      runner: async (invocation) =>
        invocation.hook === "setup"
          ? { timed_out: true, error: "deadline exceeded" }
          : { exit_code: 0 },
    });

    expect(
      result.outcomes.map((outcome) => [outcome.hook, outcome.phase, outcome.terminal]),
    ).toEqual([
      ["setup", "timeout", false],
      ["start", "completed", false],
    ]);
    expect(result.terminal).toBe(false);
    expect(result.events.map((event) => event.payload_summary.phase)).toEqual([
      "started",
      "timeout",
      "started",
      "completed",
    ]);
  });

  it("continues after non-fatal setup failures but stops on strict start failures", async () => {
    const setupResult = await executeBackgroundSessionLifecycleHooks({
      hooks: {
        setup: { command: ["./bootstrap"], failure_policy: "continue" },
        start: { command: ["autoctx", "run"] },
      },
      context,
      sequence: 50,
      timestamp,
      runner: async (invocation) =>
        invocation.hook === "setup" ? { exit_code: 17, stderr: "setup failed" } : { exit_code: 0 },
    });

    expect(
      setupResult.outcomes.map((outcome) => [outcome.hook, outcome.phase, outcome.terminal]),
    ).toEqual([
      ["setup", "failed", false],
      ["start", "completed", false],
    ]);
    expect(setupResult.terminal).toBe(false);

    const startResult = await executeBackgroundSessionLifecycleHooks({
      hooks: { start: { command: ["autoctx", "run"] } },
      context,
      sequence: 60,
      timestamp,
      runner: async () => ({ exit_code: 2, stderr: "missing runtime" }),
    });

    expect(startResult.outcomes).toHaveLength(1);
    expect(startResult.outcomes[0]).toMatchObject({
      hook: "start",
      phase: "failed",
      ok: false,
      terminal: true,
      failure_policy: "fail_session",
      exit_code: 2,
    });
    expect(startResult.terminal).toBe(true);
  });

  it("builds deterministic lifecycle hook environment without ambient secret leakage", () => {
    const env = buildLifecycleHookEnv(
      {
        session_id: "run:run-123:runtime",
        run_id: "run-123",
        task_id: "task-123",
        worker_id: "worker-1",
      },
      "start",
      { AUTOCTX_TRIGGER: "manual" },
    );

    expect(env).toEqual({
      AUTOCTX_BACKGROUND_SESSION_ID: "run:run-123:runtime",
      AUTOCTX_SESSION_ID: "run:run-123:runtime",
      AUTOCTX_RUN_ID: "run-123",
      AUTOCTX_TASK_ID: "task-123",
      AUTOCTX_WORKER_ID: "worker-1",
      AUTOCTX_HOOK_NAME: "start",
      AUTOCTX_TRIGGER: "manual",
    });
  });
});
