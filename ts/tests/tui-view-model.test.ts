import { describe, expect, it } from "vitest";

import type { ServerMessage } from "../src/server/protocol.js";
import {
  createInitialTuiViewModel,
  MAX_TUI_SEEN_EVENT_IDS,
  MAX_TUI_TRANSCRIPT_ROWS,
  reduceTuiViewModel,
  type TuiViewModel,
} from "../src/tui/view-model.js";

function feed(messages: readonly ServerMessage[]): TuiViewModel {
  return messages.reduce(
    (state, message) => reduceTuiViewModel(state, { kind: "message", message }),
    createInitialTuiViewModel("ws://example/ws/interactive"),
  );
}

function event(
  name: string,
  sequence: number,
  payload: Record<string, unknown> = {},
): Extract<ServerMessage, { type: "event" }> {
  return {
    type: "event",
    event: name,
    payload,
    event_id: `event-${sequence}`,
    sequence,
    run_id: "run-1",
  };
}

describe("TUI durable view-model reducer", () => {
  it("deduplicates replay/live overlap and orders out-of-order transcript rows", () => {
    const later = event("generation_started", 2, { generation: 2 });
    const earlier = event("run_started", 1, { scenario: "grid", target_generations: 2 });
    const model = feed([later, earlier, later]);
    expect(model.transcript.map((row) => row.sequence)).toEqual([1, 2]);
    expect(model.seenEventIds).toEqual({ "event-1": true, "event-2": true });
    expect(model.lastSequence).toBe(2);
  });

  it("does not let an older acceptance overwrite a terminal outcome", () => {
    const model = feed([
      event("run_completed", 5, { completed_generations: 2 }),
      {
        type: "run_accepted",
        run_id: "run-1",
        scenario: "grid",
        generations: 2,
        event_id: "accepted",
        sequence: 1,
      },
    ]);
    expect(model.run).toMatchObject({ active: false, outcome: "completed" });
    expect(model.transcript.map((row) => row.sequence)).toEqual([1, 5]);
  });

  it("resets every run-scoped cursor and presentation surface for a second run", () => {
    const firstPlan = {
      run_id: "run-1",
      plan_id: "plan-1",
      version: 3,
      plan_revision: 9,
      update_kind: "replan" as const,
      active_step_id: "inspect",
      steps: [{ id: "inspect", label: "Inspect", status: "in_progress" as const }],
    };
    const model = feed([
      {
        type: "run_accepted",
        run_id: "run-1",
        client_run_id: "client-1",
        scenario: "grid",
        generations: 2,
        event_id: "accepted-1",
        sequence: 1,
      },
      {
        ...event("task_plan_updated", 2, firstPlan),
        client_run_id: "client-1",
      },
      {
        ...event("run_completed", 5, { completed_generations: 2 }),
        client_run_id: "client-1",
      },
      {
        type: "state",
        active: true,
        paused: false,
        client_run_id: "client-2",
        run_id: "run-2",
        scenario: "incident",
        phase: "queued",
        event_id: "state-2",
        sequence: 1,
      },
      {
        type: "run_accepted",
        run_id: "run-2",
        client_run_id: "client-2",
        scenario: "incident",
        generations: 1,
        event_id: "accepted-2",
        sequence: 2,
      },
    ]);

    expect(model.run).toMatchObject({
      active: true,
      clientRunId: "client-2",
      runId: "run-2",
      scenario: "incident",
      outcome: null,
    });
    expect(model.lastSequence).toBe(2);
    expect(model.taskPlan).toBeNull();
    expect(model.progressNotes).toEqual([]);
    expect(model.pendingDecisions).toEqual([]);
    expect(model.receipts).toEqual({});
    expect(model.seenEventIds).toEqual({ "accepted-2": true });
    expect(model.retiredClientRunIds).toEqual(["client-1"]);
    expect(model.transcript.map((row) => row.id)).toEqual(["accepted-2"]);
  });

  it("does not let a delayed old-scope frame switch an active second run back", () => {
    const current = feed([{
      type: "run_accepted",
      run_id: "run-2",
      client_run_id: "client-2",
      scenario: "incident",
      generations: 1,
      event_id: "accepted-2",
      sequence: 1,
    }]);
    const delayed = reduceTuiViewModel(current, {
      kind: "message",
      message: {
        ...event("run_completed", 20, { completed_generations: 2 }),
        client_run_id: "client-1",
      },
    });
    expect(delayed).toBe(current);
  });

  it("never returns to a retired scope after the current run terminates", () => {
    const current = feed([
      {
        type: "run_accepted",
        run_id: "run-1",
        client_run_id: "client-1",
        scenario: "grid",
        generations: 1,
        event_id: "accepted-1",
        sequence: 1,
      },
      {
        ...event("run_completed", 2, { completed_generations: 1 }),
        client_run_id: "client-1",
      },
      {
        type: "run_accepted",
        run_id: "run-2",
        client_run_id: "client-2",
        scenario: "incident",
        generations: 1,
        event_id: "accepted-2",
        sequence: 1,
      },
      {
        ...event("run_completed", 2, { completed_generations: 1 }),
        event_id: "completed-2",
        client_run_id: "client-2",
        run_id: "run-2",
      },
    ]);
    const delayedAcceptance = reduceTuiViewModel(current, {
      kind: "message",
      message: {
        type: "run_accepted",
        run_id: "run-1",
        client_run_id: "client-1",
        scenario: "grid",
        generations: 1,
        event_id: "late-accepted-1",
        sequence: 3,
      },
    });
    expect(delayedAcceptance).toBe(current);
    expect(delayedAcceptance.run).toMatchObject({
      clientRunId: "client-2",
      runId: "run-2",
      outcome: "completed",
    });
  });

  it("adopts an unsequenced reconnect bootstrap state for a new server-current run", () => {
    const prior = feed([{
      type: "run_accepted",
      run_id: "run-1",
      client_run_id: "client-1",
      scenario: "grid",
      generations: 1,
      event_id: "accepted-1",
      sequence: 1,
    }]);
    const reconnected = reduceTuiViewModel(prior, {
      kind: "message",
      message: {
        type: "state",
        active: true,
        paused: false,
        client_run_id: "client-2",
        run_id: "run-2",
        scenario: "incident",
      },
    });
    expect(reconnected.run).toMatchObject({
      active: true,
      clientRunId: "client-2",
      runId: "run-2",
    });
    expect(reconnected.lastSequence).toBe(0);
    expect(reconnected.retiredClientRunIds).toEqual(["client-1"]);
  });

  it("converges live and replay orders on latest plan and terminal state", () => {
    const initial = event("task_plan_updated", 2, {
      run_id: "run-1",
      plan_id: "plan-1",
      version: 1,
      plan_revision: 1,
      update_kind: "initial",
      active_step_id: "inspect",
      steps: [{ id: "inspect", label: "Inspect", status: "in_progress" }],
    });
    const replanned = event("task_plan_updated", 4, {
      run_id: "run-1",
      plan_id: "plan-1",
      version: 2,
      plan_revision: 2,
      update_kind: "replan",
      active_step_id: null,
      summary: "Done",
      steps: [{ id: "inspect", label: "Inspect", status: "completed" }],
    });
    const completed = event("run_completed", 5, { completed_generations: 1 });
    const live = feed([initial, replanned, completed]);
    const replay = feed([completed, initial, replanned]);
    expect(replay.taskPlan).toEqual(live.taskPlan);
    expect(replay.run.outcome).toBe("completed");
    expect(replay.transcript.map((row) => row.id)).toEqual(live.transcript.map((row) => row.id));
  });

  it("bounds progress notes, preserves stable evidence, and redacts credentials", () => {
    const messages = Array.from({ length: 101 }, (_, index) => event(
      "agent_progress_note",
      index + 1,
      {
        run_id: "run-1",
        generation: index,
        kind: index === 100 ? "verification" : "discovery",
        text: index === 100 ? "API_KEY=top-secret verified" : `Found ${index}`,
        evidence_targets: [{ kind: "action", action_id: `action-${index}` }],
      },
    ));
    const model = feed(messages);
    expect(model.progressNotes).toHaveLength(100);
    expect(model.progressNotes[0]!.generation).toBe(1);
    expect(model.progressNotes.at(-1)!.text).not.toContain("top-secret");
    expect(model.progressNotes.at(-1)!.evidence_targets).toEqual([
      { kind: "action", action_id: "action-100" },
    ]);
  });

  it("degrades unknown events safely without including payloads", () => {
    const model = feed([event("future_private_tool_payload", 1, {
      private_scratchpad: "do not show",
      api_key: "secret",
    })]);
    expect(model.transcript[0]).toMatchObject({
      kind: "unknown",
      title: "Event: future_private_tool_payload",
    });
    expect(JSON.stringify(model)).not.toContain("do not show");
    expect(JSON.stringify(model)).not.toContain("secret");
  });

  it("renders action metadata without private prompts, responses, or artifact payloads", () => {
    const model = feed([event("action_detail", 1, {
      action_id: "action-1",
      name: "Inspect release",
      role: "architect",
      status: "completed",
      duration_ms: 12,
      input: "private chain of thought",
      output: "secret provider response",
      artifacts: [{ artifact_id: "report-1", content: "private artifact" }],
    })]);
    expect(model.transcript[0]).toMatchObject({
      kind: "progress",
      title: "Action · Inspect release",
      detail: "action_id=action-1 · role=architect · status=completed · duration_ms=12 · artifacts=1",
    });
    expect(JSON.stringify(model)).not.toContain("private chain of thought");
    expect(JSON.stringify(model)).not.toContain("secret provider response");
    expect(JSON.stringify(model)).not.toContain("private artifact");
  });

  it("keeps known execution lifecycle rows visible", () => {
    const model = feed([
      event("agents_started", 1, { generation: 1, roles: ["architect", "analyst"] }),
      event("match_completed", 2, { generation: 1, match_index: 2, score: 0.75 }),
      event("generation_timing", 3, { generation: 1, elapsed_seconds: 4.2 }),
    ]);
    expect(model.transcript.map((row) => row.title)).toEqual([
      "Agents started",
      "Match completed",
      "Generation timing",
    ]);
  });

  it("bounds retained transcript rows and replay deduplication ids", () => {
    const messages = Array.from({ length: MAX_TUI_SEEN_EVENT_IDS + 4 }, (_, index) =>
      event("generation_started", index + 1, { generation: index + 1 }));
    const model = feed(messages);
    expect(model.transcript).toHaveLength(MAX_TUI_TRANSCRIPT_ROWS);
    expect(model.transcript[0]!.sequence).toBe(messages.length - MAX_TUI_TRANSCRIPT_ROWS + 1);
    expect(Object.keys(model.seenEventIds)).toHaveLength(MAX_TUI_SEEN_EVENT_IDS);
    expect(model.seenEventIds["event-1"]).toBeUndefined();
    expect(model.seenEventIds[`event-${messages.length}`]).toBe(true);
  });

  it("classifies runtime transcript rows for activity filtering without retaining payloads", () => {
    const model = feed([event("runtime_session_event", 1, {
      session_id: "runtime-1",
      event: {
        event_type: "shell_command",
        payload: { command: "npm test", error: "failed", private: "do-not-render" },
      },
    })]);
    expect(model.transcript[0]!.activity).toEqual({
      family: "runtime",
      focus: "command",
      hasError: true,
    });
    expect(JSON.stringify(model.transcript[0])).not.toContain("do-not-render");
  });

  it("marks failed and skipped lifecycle rows for the errors activity filter", () => {
    const model = feed([
      event("playbook_update_skipped", 1, { reason: "guarded" }),
      event("run_failed", 2, { error: "provider unavailable" }),
    ]);
    expect(model.transcript.map((row) => row.activity.hasError)).toEqual([true, true]);
  });

  it("rejects hello messages that omit transcript negotiation", () => {
    const model = feed([{
      type: "hello",
      protocol_version: 1,
      capabilities: ["run_transcript_v1"],
    }]);
    expect(model.protocolCompatible).toBe(false);
    expect(model.connection).toMatchObject({
      status: "failed",
      error: "Interactive protocol version is unsupported",
    });
  });

  it("retains the last transcript while disconnected", () => {
    const connected = feed([event("run_started", 1, { scenario: "grid" })]);
    const disconnected = reduceTuiViewModel(connected, {
      kind: "connection",
      status: "reconnecting",
      attempt: 2,
      error: "socket closed",
    });
    expect(disconnected.transcript).toEqual(connected.transcript);
    expect(disconnected.connection).toMatchObject({ status: "reconnecting", attempt: 2 });
  });

  it("turns pending playbooks into safe, actionable decisions", () => {
    const model = feed([event("playbook_pending", 4, { scenario: "grid_ctf" })]);
    expect(model.pendingDecisions).toEqual([
      expect.objectContaining({ kind: "playbook_approval", scenario: "grid_ctf" }),
    ]);
    expect(model.transcript.at(-1)).toMatchObject({
      kind: "decision",
      detail: "Use /approve grid_ctf confirm or /reject grid_ctf confirm.",
    });
    expect(reduceTuiViewModel(model, {
      kind: "decision_resolved",
      scenario: "grid_ctf",
    }).pendingDecisions).toEqual([]);
  });
});
