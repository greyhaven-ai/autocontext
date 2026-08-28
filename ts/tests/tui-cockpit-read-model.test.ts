import { describe, expect, it, vi } from "vitest";

import { buildQueueCockpitState } from "../src/server/cockpit-api.js";
import type { RuntimeSessionReadStore } from "../src/session/runtime-session-read-model.js";
import type { TaskQueueRow } from "../src/storage/index.js";
import { TuiReadModelClient } from "../src/tui/read-model-client.js";

const NOW = new Date("2026-08-14T20:00:00.000Z");

function task(overrides: Partial<TaskQueueRow>): TaskQueueRow {
  return {
    id: "task-1",
    spec_name: "support",
    status: "pending",
    priority: 0,
    config_json: null,
    scheduled_at: null,
    started_at: null,
    completed_at: null,
    best_score: null,
    best_output: null,
    total_rounds: null,
    met_threshold: 0,
    result_json: null,
    error: null,
    created_at: "2026-08-14 19:00:00",
    updated_at: "2026-08-14 19:59:00",
    attempts: 0,
    ...overrides,
  };
}

describe("queue/worker cockpit read model", () => {
  it("distinguishes every queue reliability state", () => {
    const rows = [
      task({ id: "waiting" }),
      task({ id: "running", status: "running", attempts: 1 }),
      task({ id: "retrying", attempts: 1, scheduled_at: "2026-08-14 19:59:00" }),
      task({ id: "backoff", attempts: 1, scheduled_at: "2026-08-14 20:05:00" }),
      task({ id: "recovered", attempts: 1 }),
      task({ id: "failed", status: "failed", attempts: 1 }),
      task({ id: "dead", status: "failed", attempts: 3 }),
      task({ id: "complete", status: "completed", attempts: 1 }),
      task({ id: "stale", status: "running", attempts: 1, updated_at: "2026-08-14 18:00:00" }),
    ];
    const state = buildQueueCockpitState(rows, null, NOW);
    expect(Object.fromEntries(state.tasks.map((entry) => [entry.id, entry.state]))).toEqual({
      waiting: "waiting",
      running: "running",
      retrying: "retrying",
      backoff: "backoff",
      recovered: "recovered",
      failed: "failed",
      dead: "dead_letter",
      complete: "completed",
      stale: "stale",
    });
  });

  it("shows connected, disconnected, and truthful empty worker states", () => {
    const runtimeStore = {
      list: () => [
        {
          sessionId: "session-live",
          parentSessionId: "",
          taskId: "running",
          workerId: "worker-live",
          metadata: {},
          events: [{ type: "progress" }],
          createdAt: "2026-08-14T19:59:00.000Z",
          updatedAt: "2026-08-14T19:59:30.000Z",
        },
        {
          sessionId: "session-old",
          parentSessionId: "",
          taskId: "old-task",
          workerId: "worker-old",
          metadata: {},
          events: [],
          createdAt: "2026-08-14T18:00:00.000Z",
          updatedAt: "2026-08-14T18:00:00.000Z",
        },
      ],
      load: () => null,
    } as unknown as RuntimeSessionReadStore;
    const state = buildQueueCockpitState([
      task({ id: "running", status: "running", error: "last failure" }),
    ], runtimeStore, NOW);
    expect(state.workers).toEqual([
      expect.objectContaining({ worker_id: "worker-live", state: "connected", current_task_id: "running", failure_summary: "last failure" }),
      expect.objectContaining({ worker_id: "worker-old", state: "disconnected", current_task_id: null }),
    ]);
    expect(buildQueueCockpitState([], null, NOW).workers).toEqual([]);
  });
});

describe("TUI HTTP read-model adapter", () => {
  it.each([
    [404, "not_found"],
    [501, "unsupported"],
    [500, "server_error"],
  ] as const)("maps HTTP %i to %s", async (status, kind) => {
    const client = new TuiReadModelClient("http://localhost:8000", {
      fetchImpl: vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "specific" }), { status })),
    });
    await expect(client.runStatus("missing")).resolves.toEqual({
      ok: false,
      kind,
      status,
      detail: "specific",
    });
  });

  it("distinguishes an unavailable server", async () => {
    const client = new TuiReadModelClient("http://localhost:8000", {
      fetchImpl: vi.fn().mockRejectedValue(new Error("ECONNREFUSED")),
    });
    await expect(client.listRuns()).resolves.toMatchObject({
      ok: false,
      kind: "unavailable",
    });
  });

  it("sends playbook decisions through the canonical HTTP routes", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "approved" }), {
      status: 200,
    }));
    const client = new TuiReadModelClient("http://localhost:8000", { fetchImpl });

    await expect(client.approvePlaybook("grid ctf")).resolves.toMatchObject({ ok: true });
    expect(fetchImpl).toHaveBeenCalledWith(
      new URL("http://localhost:8000/api/knowledge/grid%20ctf/playbook/approve"),
      { method: "POST" },
    );
  });

  it("authenticates HTTP reads without putting credentials in the URL", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response("[]", { status: 200 }));
    const client = new TuiReadModelClient("https://host.example", {
      fetchImpl,
      authToken: "0123456789abcdef0123456789abcdef",
    });

    await expect(client.listRuns()).resolves.toMatchObject({ ok: true });
    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url.toString()).not.toContain("token");
    expect(new Headers(init.headers).get("Authorization"))
      .toBe("Bearer 0123456789abcdef0123456789abcdef");
  });

  it("watches changing snapshots until terminal state", async () => {
    const bodies = ["running", "running", "completed"].map((status, index) => ({
      run_id: "run-1",
      scenario_name: "grid",
      target_generations: 2,
      status,
      created_at: "now",
      generations: Array.from({ length: index }, (_, generation) => ({ generation })),
    }));
    const fetchImpl = vi.fn().mockImplementation(() => Promise.resolve(new Response(
      JSON.stringify(bodies.shift()),
      { status: 200 },
    )));
    const updates: string[] = [];
    const client = new TuiReadModelClient("http://localhost:8000", {
      fetchImpl,
      watchIntervalMs: 1,
    });
    const result = await client.watchRun("run-1", {
      onUpdate: (status) => updates.push(`${status.status}:${status.generations.length}`),
    });
    expect(result).toMatchObject({ ok: true, value: { status: "completed" } });
    expect(updates).toEqual(["running:0", "running:1", "completed:2"]);
  });

  it("aborts an in-flight watch request when the operator detaches", async () => {
    const fetchImpl = vi.fn().mockImplementation((_url: URL, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(new DOMException("aborted", "AbortError"));
        }, { once: true });
      }));
    const client = new TuiReadModelClient("http://localhost:8000", { fetchImpl });
    const controller = new AbortController();
    const result = client.watchRun("run-1", { signal: controller.signal });
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalledOnce());
    controller.abort("detached");
    await expect(result).resolves.toEqual({
      ok: false,
      kind: "unavailable",
      detail: "watch detached",
    });
  });
});
