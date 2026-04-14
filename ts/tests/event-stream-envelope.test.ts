import { describe, expect, it } from "vitest";

import {
  buildEventStreamEnvelope,
  buildGenerationEventEnvelope,
  buildMissionProgressEventEnvelope,
} from "../src/server/event-stream-envelope.js";

describe("event stream envelope", () => {
  it("builds a generic event-stream envelope with timestamp and version", () => {
    expect(buildEventStreamEnvelope({
      channel: "generation",
      event: "run_started",
      payload: { run_id: "run_1" },
      timestamp: "2026-04-09T14:00:00.000Z",
    })).toEqual({
      channel: "generation",
      event: "run_started",
      payload: { run_id: "run_1" },
      ts: "2026-04-09T14:00:00.000Z",
      v: 1,
    });
  });

  it("builds generation event envelopes", () => {
    expect(buildGenerationEventEnvelope(
      "generation_completed",
      { generation: 3 },
      "2026-04-09T14:00:01.000Z",
    )).toEqual({
      channel: "generation",
      event: "generation_completed",
      payload: { generation: 3 },
      ts: "2026-04-09T14:00:01.000Z",
      v: 1,
    });
  });

  it("builds mission progress event envelopes", () => {
    expect(buildMissionProgressEventEnvelope({
      type: "mission_progress",
      missionId: "mission_1",
      status: "paused",
      stepsCompleted: 2,
      budgetUsed: 2,
      budgetMax: 5,
    }, "2026-04-09T14:00:02.000Z")).toEqual({
      channel: "mission",
      event: "mission_progress",
      payload: {
        type: "mission_progress",
        missionId: "mission_1",
        status: "paused",
        stepsCompleted: 2,
        budgetUsed: 2,
        budgetMax: 5,
      },
      ts: "2026-04-09T14:00:02.000Z",
      v: 1,
    });
  });
});
