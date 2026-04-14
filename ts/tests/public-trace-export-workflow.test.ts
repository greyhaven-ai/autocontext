import { describe, expect, it } from "vitest";

import { ActorRef, RunTrace, TraceEvent } from "../src/analytics/run-trace.js";
import {
  exportRunTraceToPublicTrace,
  mapRunTraceEventToPublicMessage,
} from "../src/traces/public-trace-export-workflow.js";

describe("public trace export workflow", () => {
  it("maps internal events to public roles and preserves payload metadata", () => {
    const event = new TraceEvent({
      eventType: "role_completed",
      actor: new ActorRef("agent", "competitor", "competitor"),
      payload: { output: "my strategy", score: 0.8 },
    });

    expect(mapRunTraceEventToPublicMessage(event)).toMatchObject({
      role: "assistant",
      content: "my strategy",
      metadata: {
        eventType: "role_completed",
        internalRole: "competitor",
        score: 0.8,
      },
    });
  });

  it("exports run traces and adds a fallback message when no events exist", () => {
    const trace = new RunTrace("run_001", "grid_ctf");
    trace.addEvent(new TraceEvent({
      eventType: "generation_started",
      actor: new ActorRef("system", "harness", "autocontext"),
      payload: { generation: 1 },
    }));

    const exported = exportRunTraceToPublicTrace(trace, {
      sourceHarness: "autocontext",
      model: "claude-sonnet-4-20250514",
      provider: "anthropic",
    });
    expect(exported).toMatchObject({
      traceId: "trace_run_001",
      sessionId: "run_001",
      sourceHarness: "autocontext",
      metadata: {
        model: "claude-sonnet-4-20250514",
        provider: "anthropic",
        scenarioType: "grid_ctf",
        eventCount: 1,
      },
    });
    expect(exported.messages[0]?.role).toBe("system");

    const emptyTrace = new RunTrace("run_empty", "operator_loop");
    const fallback = exportRunTraceToPublicTrace(emptyTrace, { sourceHarness: "autocontext" });
    expect(fallback.messages).toHaveLength(1);
    expect(fallback.messages[0]?.content).toBe("Trace run_empty for operator_loop");
  });
});
