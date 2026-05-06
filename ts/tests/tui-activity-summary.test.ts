import { describe, expect, it } from "vitest";

import {
  DEFAULT_TUI_ACTIVITY_SETTINGS,
  summarizeTuiEvent,
} from "../src/tui/activity-summary.js";

describe("TUI activity summary", () => {
  it("keeps existing run lifecycle summaries stable", () => {
    expect(
      summarizeTuiEvent("run_started", {
        run_id: "run-123",
        scenario: "support_triage",
      }),
    ).toBe("run run-123 started for support_triage");
  });

  it("summarizes live runtime-session prompt events for the operator timeline", () => {
    expect(
      summarizeTuiEvent("runtime_session_event", {
        session_id: "run:run-123:runtime",
        event: {
          event_type: "prompt_submitted",
          sequence: 0,
          payload: {
            role: "architect",
            prompt: "Improve the operator-facing runtime timeline",
          },
        },
      }),
    ).toBe(
      "runtime run:run-123:runtime #0 prompt role=architect prompt=Improve the operator-facing runtime timeline",
    );
  });

  it("summarizes live runtime-session assistant and child-task events", () => {
    expect(
      summarizeTuiEvent("runtime_session_event", {
        session_id: "run:run-123:runtime",
        event: {
          event_type: "assistant_message",
          sequence: 1,
          payload: {
            role: "architect",
            text: "Group prompts, command events, and child tasks.",
          },
        },
      }),
    ).toBe(
      "runtime run:run-123:runtime #1 assistant role=architect text=Group prompts, command events, and child tasks.",
    );

    expect(
      summarizeTuiEvent("runtime_session_event", {
        session_id: "run:run-123:runtime",
        event: {
          event_type: "child_task_completed",
          sequence: 4,
          payload: {
            taskId: "task-1",
            childSessionId: "task:run:run-123:runtime:task-1",
            result: "Verified edge-case coverage",
          },
        },
      }),
    ).toBe(
      "runtime run:run-123:runtime #4 child completed task=task-1 child=task:run:run-123:runtime:task-1 result=Verified edge-case coverage",
    );
  });

  it("filters live runtime-session activity by operator focus", () => {
    const promptEvent = {
      session_id: "run:run-123:runtime",
      event: {
        event_type: "prompt_submitted",
        sequence: 0,
        payload: {
          role: "architect",
          prompt: "Improve the operator timeline",
        },
      },
    };
    const commandEvent = {
      session_id: "run:run-123:runtime",
      event: {
        event_type: "shell_command",
        sequence: 2,
        payload: {
          command: "npm test",
          exitCode: 0,
        },
      },
    };

    expect(
      summarizeTuiEvent("runtime_session_event", promptEvent, {
        filter: "commands",
        verbosity: "normal",
      }),
    ).toBeNull();
    expect(
      summarizeTuiEvent("runtime_session_event", commandEvent, {
        filter: "commands",
        verbosity: "normal",
      }),
    ).toBe("runtime run:run-123:runtime #2 shell command=npm test exit=0");
  });

  it("supports quiet and verbose runtime-session activity summaries", () => {
    const assistantEvent = {
      session_id: "run:run-123:runtime",
      event: {
        event_id: "event-abc",
        event_type: "assistant_message",
        sequence: 1,
        timestamp: "2026-04-10T00:00:01.000Z",
        payload: {
          role: "architect",
          text: "Group prompts, command events, and child tasks.",
        },
      },
    };

    expect(
      summarizeTuiEvent("runtime_session_event", assistantEvent, {
        filter: "all",
        verbosity: "quiet",
      }),
    ).toBe("runtime run:run-123:runtime #1 assistant role=architect");
    expect(
      summarizeTuiEvent("runtime_session_event", assistantEvent, {
        filter: "all",
        verbosity: "verbose",
      }),
    ).toBe(
      "runtime run:run-123:runtime #1 assistant role=architect text=Group prompts, command events, and child tasks. ts=2026-04-10T00:00:01.000Z event=event-abc",
    );
  });

  it("keeps default activity settings explicit", () => {
    expect(DEFAULT_TUI_ACTIVITY_SETTINGS).toEqual({
      filter: "all",
      verbosity: "normal",
    });
  });
});
