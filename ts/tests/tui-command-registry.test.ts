import { describe, expect, it, vi } from "vitest";

import { buildRunProgressReport, progressReportReference } from "../src/analytics/progress-report.js";
import { visibleSupportedCommandNames } from "../src/cli/command-registry.js";
import {
  renderRunStatus,
  type RunInspectionGeneration,
  type RunInspectionRun,
} from "../src/cli/run-inspection-command-workflow.js";
import {
  TUI_COMMAND_REGISTRY,
  formatTuiCommandHelp,
  resolveTuiCommand,
  tuiSlashCommands,
} from "../src/tui/command-registry.js";
import {
  formatTuiRunStatus,
  TuiCommandRuntime,
} from "../src/tui/registered-command-workflow.js";
import type { TuiReadModelClient } from "../src/tui/read-model-client.js";
import type { TuiSession } from "../src/tui/session.js";
import { createInitialTuiViewModel } from "../src/tui/view-model.js";

describe("canonical TUI command registry", () => {
  it("drives help, autocomplete, aliases, routing, capabilities, keys, and executors", () => {
    const help = formatTuiCommandHelp(["safe_run_stop_v1"]).join("\n");
    const completions = tuiSlashCommands(["safe_run_stop_v1"]);
    expect(completions.map((entry) => entry.name)).toEqual(
      TUI_COMMAND_REGISTRY.map((entry) => entry.name),
    );
    for (const descriptor of TUI_COMMAND_REGISTRY) {
      expect(descriptor.executor).toBe(descriptor.name);
      expect(help).toContain(`/${descriptor.name}`);
      expect(resolveTuiCommand(`/${descriptor.name} example`)?.descriptor).toBe(descriptor);
      for (const alias of descriptor.aliases) {
        expect(resolveTuiCommand(`/${alias}`)?.descriptor).toBe(descriptor);
      }
    }
    expect(help).toContain("Ctrl+C");
    expect(help).toContain("Cooperatively stop");
  });

  it("reconciles shared command vocabulary with the canonical CLI", () => {
    const cli = new Set(visibleSupportedCommandNames());
    for (const shared of ["solve", "run", "status", "watch", "show", "login", "logout", "whoami", "queue"]) {
      expect(cli.has(shared), `${shared} missing from CLI`).toBe(true);
      expect(TUI_COMMAND_REGISTRY.some((entry) =>
        entry.name === shared || entry.aliases.includes(shared))).toBe(true);
    }
    expect(resolveTuiCommand("/list")?.descriptor.name).toBe("runs");
    expect(resolveTuiCommand("/runtime-sessions")?.descriptor.name).toBe("sessions");
  });

  it("explains unavailable capability-gated commands", () => {
    expect(formatTuiCommandHelp([]).join("\n")).toContain(
      "/stop confirm",
    );
    expect(formatTuiCommandHelp([]).join("\n")).toContain(
      "unavailable: server lacks safe_run_stop_v1",
    );
  });
});

describe("registered command execution", () => {
  it("requires a run-specific confirmation before stop and keeps detach separate", async () => {
    const model = createInitialTuiViewModel("ws://example/ws/interactive");
    const activeModel = {
      ...model,
      capabilities: ["safe_run_stop_v1"],
      run: {
        ...model.run,
        active: true,
        runId: "run-123",
        clientRunId: "client-123",
      },
    };
    const stopActiveRun = vi.fn().mockResolvedValue("requested");
    const session = {
      viewModel: activeModel,
      stopActiveRun,
    } as unknown as TuiSession;
    const runtime = new TuiCommandRuntime({
      session,
      readModels: {} as TuiReadModelClient,
    });
    await expect(runtime.execute("/stop")).resolves.toEqual({
      lines: [
        "Stop run run-123? This affects the run, while /quit only detaches.",
        "Type /stop confirm to continue.",
      ],
    });
    expect(stopActiveRun).not.toHaveBeenCalled();
    await expect(runtime.execute("/stop confirm")).resolves.toEqual({
      lines: ["stop requested for run-123"],
    });
    expect(stopActiveRun).toHaveBeenCalledOnce();
    await expect(runtime.execute("/quit")).resolves.toEqual({ lines: [], shouldExit: true });
    expect(stopActiveRun).toHaveBeenCalledOnce();
  });

  it("rejects non-positive iterations before session effects", async () => {
    const model = createInitialTuiViewModel("ws://example/ws/interactive");
    const startRun = vi.fn();
    const runtime = new TuiCommandRuntime({
      session: { viewModel: model, startRun } as unknown as TuiSession,
      readModels: {} as TuiReadModelClient,
    });
    await expect(runtime.execute("/run grid 0")).resolves.toEqual({
      lines: ["usage: /run <scenario> [positive-iterations]"],
    });
    expect(startRun).not.toHaveBeenCalled();
  });

  it("requires explicit confirmation for pending playbook decisions", async () => {
    const model = createInitialTuiViewModel("ws://example/ws/interactive");
    const approvePlaybook = vi.fn().mockResolvedValue({ ok: true, value: {} });
    const rejectPlaybook = vi.fn().mockResolvedValue({ ok: true, value: {} });
    const resolvePendingDecision = vi.fn();
    const runtime = new TuiCommandRuntime({
      session: { viewModel: model, resolvePendingDecision } as unknown as TuiSession,
      readModels: { approvePlaybook, rejectPlaybook } as unknown as TuiReadModelClient,
    });

    await expect(runtime.execute("/approve grid_ctf")).resolves.toEqual({
      lines: ["usage: /approve <scenario> confirm"],
    });
    expect(approvePlaybook).not.toHaveBeenCalled();
    await expect(runtime.execute("/approve grid_ctf confirm")).resolves.toEqual({
      lines: ["approved pending playbook for grid_ctf"],
    });
    await expect(runtime.execute("/reject grid_ctf confirm")).resolves.toEqual({
      lines: ["rejected pending playbook for grid_ctf"],
    });
    expect(approvePlaybook).toHaveBeenCalledWith("grid_ctf");
    expect(rejectPlaybook).toHaveBeenCalledWith("grid_ctf");
    expect(resolvePendingDecision).toHaveBeenCalledTimes(2);
    expect(resolvePendingDecision).toHaveBeenCalledWith("grid_ctf");
  });

  it("browses both session surfaces and drills into background or runtime relationships", async () => {
    const model = createInitialTuiViewModel("ws://example/ws/interactive");
    const listBackgroundSessions = vi.fn().mockResolvedValue({
      ok: true,
      value: {
        sessions: [{
          session_id: "task:child-1",
          runtime_session_id: "runtime-child-1",
          run_id: "run-1",
          task_id: "child-1",
          parent_session_id: "runtime-parent",
          status: "running",
          goal: "inspect",
          event_count: 4,
          artifact_count: 1,
          child_session_count: 2,
          child_status_counts: {},
          created_at: "now",
          updated_at: "now",
          result_url: "/background",
          runtime_session_url: "/runtime",
        }],
      },
    });
    const listRuntimeSessions = vi.fn().mockResolvedValue({
      ok: true,
      value: {
        sessions: [{
          session_id: "runtime-parent",
          parent_session_id: "",
          task_id: "parent-1",
          worker_id: "worker-1",
          goal: "parent",
          event_count: 8,
          created_at: "now",
          updated_at: "now",
        }],
      },
    });
    const backgroundSession = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        value: {
          summary: { session_id: "task:child-1", parent_session_id: "runtime-parent" },
          child_sessions: [{ session_id: "runtime-grandchild" }],
        },
      })
      .mockResolvedValueOnce({
        ok: false,
        kind: "not_found",
        status: 404,
        detail: "not found",
      });
    const runtimeSession = vi.fn().mockResolvedValue({
      ok: true,
      value: { sessionId: "runtime-only", parentSessionId: "runtime-parent" },
    });
    const runtime = new TuiCommandRuntime({
      session: { viewModel: model } as unknown as TuiSession,
      readModels: {
        listBackgroundSessions,
        listRuntimeSessions,
        backgroundSession,
        runtimeSession,
      } as unknown as TuiReadModelClient,
    });

    const list = await runtime.execute("/sessions");
    expect(list.lines.join("\n")).toContain("task:child-1 · running · run=run-1 · parent=runtime-parent · children=2");
    expect(list.lines.join("\n")).toContain("runtime-parent · events=8 · task=parent-1 · parent=none");
    expect(listBackgroundSessions).toHaveBeenCalledOnce();
    expect(listRuntimeSessions).toHaveBeenCalledOnce();

    expect((await runtime.execute("/session task:child-1")).lines.join("\n"))
      .toContain("runtime-grandchild");
    expect((await runtime.execute("/session runtime-only")).lines.join("\n"))
      .toContain("parentSessionId: runtime-parent");
    expect(runtimeSession).toHaveBeenCalledWith("runtime-only");
  });

  it("renders artifact discoveries as OSC 8 links", async () => {
    const model = createInitialTuiViewModel("ws://example/ws/interactive");
    const runtime = new TuiCommandRuntime({
      session: { viewModel: model } as unknown as TuiSession,
      readModels: {
        baseUrl: "http://localhost:8000",
        runInspection: vi.fn().mockResolvedValue({
          ok: true,
          value: { artifact_discovery: { export: "/api/export/run-1" } },
        }),
      } as unknown as TuiReadModelClient,
    });
    const result = await runtime.execute("/artifacts run-1");
    expect(result.lines).toEqual([
      "export: \u001b]8;;http://localhost:8000/api/export/run-1\u001b\\http://localhost:8000/api/export/run-1\u001b]8;;\u001b\\",
    ]);
  });

  it("uses the same status presenter as the CLI for an identical fixture", () => {
    const run: RunInspectionRun = {
      run_id: "run-1",
      scenario: "grid_ctf",
      target_generations: 3,
      executor_mode: "sequential",
      status: "running",
      agent_provider: "anthropic",
      created_at: "2026-08-14T20:00:00.000Z",
      updated_at: "2026-08-14T20:01:00.000Z",
    };
    const generation: RunInspectionGeneration = {
      generation_index: 1,
      mean_score: 0.6,
      best_score: 0.7,
      elo: 1100,
      wins: 1,
      losses: 0,
      gate_decision: "advance",
      status: "completed",
      duration_seconds: 12,
      evaluator_epoch: null,
      quarantined: null,
      created_at: "2026-08-14T20:00:00.000Z",
      updated_at: "2026-08-14T20:01:00.000Z",
    };
    const progress = buildRunProgressReport({
      runId: run.run_id,
      threshold: 0.65,
      generatedAt: "2026-08-14T20:01:00.000Z",
      events: [{
        event_id: "event-1",
        event_type: "evaluation_completed",
        timestamp: "2026-08-14T20:00:30.000Z",
        generation_index: 1,
        score: 0.7,
      }],
    });
    const runtimeSession = {
      session_id: "run:run-1:runtime",
      parent_session_id: "",
      task_id: "task-1",
      worker_id: "worker-1",
      goal: "autoctx run grid_ctf",
      event_count: 2,
      created_at: run.created_at,
      updated_at: run.updated_at,
    };

    const cliLines = renderRunStatus(run, [generation], false, runtimeSession, progress).split("\n");
    const tuiLines = formatTuiRunStatus({
      run_id: run.run_id,
      scenario_name: run.scenario,
      target_generations: run.target_generations,
      status: run.status,
      created_at: run.created_at,
      generations: [{
        generation: generation.generation_index,
        mean_score: generation.mean_score,
        best_score: generation.best_score,
        elo: generation.elo,
        wins: generation.wins,
        losses: generation.losses,
        gate_decision: generation.gate_decision,
        status: generation.status,
        duration_seconds: generation.duration_seconds,
        evaluator_epoch: generation.evaluator_epoch,
        quarantined: generation.quarantined,
      }],
      runtime_session: runtimeSession,
      progress_report: progressReportReference(progress),
    });
    expect(tuiLines).toEqual(cliLines);
  });
});
